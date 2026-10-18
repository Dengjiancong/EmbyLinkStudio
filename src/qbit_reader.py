"""Read-only qBittorrent Web API adapter, configured only by the NAS administrator."""
import http.cookiejar,json,os,posixpath,time,urllib.parse,urllib.request
from pathlib import PurePosixPath,Path
COMPLETE_STATES={"uploading","stalledUP","pausedUP","queuedUP","forcedUP","stoppedUP"}
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class QbitReader:
    def __init__(self,config):
        self.config=config;self.entries={};self.checked=0;self.scope=None
        self.url=config.get("url","").rstrip("/");p=urllib.parse.urlsplit(self.url)
        if p.scheme not in ("http","https") or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError("qB 地址配置无效")
        self.mappings=[]
        for m in config.get("path_mappings",[]):
            remote=PurePosixPath(m["remote"]);local=Path(m["local"])
            if not remote.is_absolute() or ".." in remote.parts or not local.is_absolute() or ".." in local.parts:raise ValueError("qB 路径映射需为无 .. 的绝对路径")
            self.mappings.append((remote,local))
        if not self.mappings:raise ValueError("qB 尚未配置下载路径映射")
        self.unmatched=config.get("unmatched","defer")
        if self.unmatched not in ("defer","heuristic"):raise ValueError("qB unmatched 应为 defer 或 heuristic")
    def mapped(self,remote):
        p=PurePosixPath(remote)
        if not p.is_absolute() or ".." in p.parts:return None
        matches=[(r,l) for r,l in self.mappings if p==r or r in p.parents]
        if not matches:return None
        r,l=max(matches,key=lambda item:len(item[0].parts))
        return str(l/str(p.relative_to(r)))
    def refresh(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),NoRedirect())
        def call(endpoint,data=None):
            req=urllib.request.Request(self.url+"/api/v2/"+endpoint,data=None if data is None else urllib.parse.urlencode(data).encode(),headers={"Referer":self.url+"/","Content-Type":"application/x-www-form-urlencoded"})
            with opener.open(req,timeout=15) as response:
                payload=response.read(32*1024*1024+1)
                if len(payload)>32*1024*1024:raise ValueError()
                return payload
        try:
            username=os.environ.get(self.config.get("username_env","QBIT_USERNAME"),"")
            password=os.environ.get(self.config.get("password_env","QBIT_PASSWORD"),"")
            if username or password:
                if call("auth/login",{"username":username,"password":password}).strip()!=b"Ok.":raise ValueError()
            torrents=json.loads(call("torrents/info"))
            if not isinstance(torrents,list):raise ValueError()
            entries={}
            for torrent in torrents:
                save=torrent.get("save_path","")
                # Do not query file lists for downloads outside configured mappings.
                content=torrent.get("content_path") or posixpath.join(save,torrent.get("name",""))
                mapped=self.mapped(content)
                if self.mapped(save) is None and mapped is None:continue
                if self.scope is not None and mapped is not None:
                    local=Path(mapped)
                    if local!=self.scope and local not in self.scope.parents and self.scope not in local.parents:continue
                complete=(torrent.get("state") in COMPLETE_STATES and torrent.get("amount_left")==0 and torrent.get("progress",0)>=1)
                rows=json.loads(call("torrents/files?"+urllib.parse.urlencode({"hash":torrent["hash"]})))
                for row in rows:
                    name=PurePosixPath(row.get("name",""))
                    if name.is_absolute() or ".." in name.parts:continue
                    path=self.mapped(str(PurePosixPath(save)/name))
                    if path is None:continue
                    ready=complete and row.get("progress",0)>=1 and row.get("priority",0)>0
                    item={"ready":ready,"size":row.get("size"),"state":torrent.get("state","unknown")}
                    if path in entries:item["ready"]=item["ready"] and entries[path]["ready"]
                    entries[path]=item
            self.entries=entries;self.checked=time.monotonic()
        except Exception as error:
            if hasattr(error,"close"):error.close()
            self.entries={};self.checked=0
            raise RuntimeError("qBittorrent 状态读取失败：检查 NAS 端地址、环境变量凭据、WebUI 权限和路径映射；本次不绕过下载检查。") from None
    def check(self,path,size=None):
        if not self.checked or time.monotonic()-self.checked>=5:self.refresh()
        entry=self.entries.get(str(path))
        if not entry:
            return (self.unmatched=="heuristic","qB 未匹配：回退静置判断" if self.unmatched=="heuristic" else "qB 未匹配该文件，暂缓；检查路径映射")
        if not entry["ready"]:return False,"qB 任务未完成、未选中或处于校验/移动等状态："+entry["state"]
        if size is not None and entry["size"]!=size:return False,"qB 文件大小与实际不一致，暂缓"
        return True,"qB 已确认此文件完成"
