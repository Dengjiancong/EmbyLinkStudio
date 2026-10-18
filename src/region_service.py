"""Administrator-defined regions, directory browsing and persistent job history."""
import json
import os
import re
import stat
import threading
import time
from pathlib import Path
from nas_agent import Executor,safe_directory,append_journal
from safe_fs import physical_overlap,private_reader,ensure_tree,PINNED
from job_status import classify
from media_features import validate_options
from advanced_service import AdvancedService
from qbit_reader import QbitReader
from link_rules import VERSION,excluded_path,validate_budget

def overlap(a,b):return a==b or a in b.parents or b in a.parents
def relative_path(value):
    if not isinstance(value,str) or value.startswith(("/","\\")) or "\\" in value:
        raise ValueError("只能使用区域内相对目录，不允许绝对路径")
    if value and any(part in ("",".","..") for part in value.split("/")):
        raise ValueError("不允许 .. 或空路径片段")
    if excluded_path(value):raise ValueError("不允许选择回收站")
    return value
def within(root,relative,existing=False):
    relative_path(relative);path=root/relative
    # Check every existing ancestor; no traversal through symlinks.
    current=root
    safe_directory(root.parent)
    for part in ("",)+tuple(relative.split("/") if relative else []):
        if part:current=current/part
        if current.exists() or current.is_symlink():safe_directory(current)
        elif existing:raise FileNotFoundError("目录不存在："+str(current))
    return path

class RegionService(AdvancedService):
    def __init__(self,config,state,protection=None,metadata_enabled=True):
        self.metadata_enabled=bool(metadata_enabled)
        self.state=Path(os.path.abspath(state))
        self.delete_guard=protection or {"active":False}
        self.regions={};self.operation_lock=threading.Lock();self.plan_engines={};self.jobs={}
        self.meta_lock=threading.Lock();self.observations={};self.cancel_events={};self.audits={};self.downloaders={}
        rows=config.get("regions") if isinstance(config,dict) else None
        if not isinstance(rows,list) or not 1<=len(rows)<=32:raise ValueError("regions must contain 1～32 entries")
        for name,item in config.get("downloaders",{}).items():self.downloaders[name]=QbitReader(item)
        for item in rows:
            rid=item.get("id","")
            if not isinstance(rid,str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",rid) or rid in self.regions:
                raise ValueError("区域 ID 无效或重复")
            region=dict(item)
            for key in ("source_root","destination_root"):
                raw=region.get(key)
                if not isinstance(raw,str) or not Path(raw).is_absolute() or ".." in Path(raw).parts:
                    raise ValueError("区域根目录必须为绝对路径")
                region[key]=str(Path(os.path.abspath(raw)))
                if excluded_path(Path(raw).as_posix()):raise ValueError("回收站不能作为区域")
            if not isinstance(region.get("name"),str) or not region["name"].strip():raise ValueError("区域名称不能为空")
            source=Path(region["source_root"]);dest=Path(region["destination_root"])
            if overlap(source,dest):raise ValueError("原件和入口范围不能重叠")
            safe_directory(source);safe_directory(dest.parent)
            if dest.exists() or dest.is_symlink():safe_directory(dest)
            for key in ("default_source_relative","default_dest_relative"):
                region[key]=relative_path(region.get(key,""))
            region.setdefault("source_display",str(source));region.setdefault("destination_display",str(dest))
            for key in ('source_display','destination_display'):
                if not isinstance(region[key],str) or len(region[key])>4096:raise ValueError('区域路径说明无效')
            self.regions[rid]=region
        for region in self.regions.values():
            for root in (Path(region["source_root"]),Path(region["destination_root"])):
                if overlap(root,self.state) or physical_overlap(root,self.state):raise ValueError("记录目录不能与媒体区域重叠")
            for other in self.regions.values():
                if overlap(Path(region["source_root"]),Path(other["destination_root"])) or physical_overlap(Path(region["source_root"]),Path(other["destination_root"])):
                    raise ValueError("某区域入口与另一原件范围重叠")
        safe_directory(self.state.parent)
        if self.state.exists() or self.state.is_symlink():safe_directory(self.state)
    @classmethod
    def from_environment(cls,protection):
        filename=os.environ.get("REGIONS_FILE")
        if filename:
            with open(filename,encoding="utf-8-sig") as f:config=json.load(f)
        else:
            config={"regions":[{"id":"legacy","name":"旧版固定测试区域","source_root":os.environ["SOURCE_ROOT"],"destination_root":os.environ["DEST_ROOT"]}]}
        return cls(config,os.environ.get("STATE_ROOT","/state"),protection,metadata_enabled=os.environ.get("ENABLE_METADATA_WRITES","false").lower()=="true")
    def downloader_for(self,region,selection):
        if not selection.get("use_downloader") and not region.get("require_downloader"):return None
        reader=self.downloaders.get(region.get("downloader"))
        if reader is None:raise ValueError("该区域未配置 qBittorrent 连接，请管理员配置 downloaders 和路径映射")
        scoped=QbitReader(reader.config);scoped.scope=Path(region["source_root"])/selection["source_relative"]
        return scoped
    def health(self):
        return {"version":VERSION,"backend":"NAS-local os.link","delete_guard":self.delete_guard,"region_count":len(self.regions),"features":{"metadata_writes":self.metadata_enabled,"pinned_directory_io":PINNED}}
    def region_list(self):
        fields={"id","name","source_root","destination_root","source_display","destination_display","default_source_relative","default_dest_relative","downloader","require_downloader"}
        return {"regions":[{k:v for k,v in region.items() if k in fields} for region in self.regions.values()]}
    def get_region(self,rid):
        if not isinstance(rid,str) or rid not in self.regions:raise ValueError("未知存储区域，请重新连接")
        return self.regions[rid]
    def browse(self,data):
        if not isinstance(data,dict) or set(data)-{"region_id","side","relative"}:raise ValueError("浏览参数无效")
        region=self.get_region(data.get("region_id"));side=data.get("side")
        if side not in ("source","destination"):raise ValueError("浏览范围无效")
        root=Path(region["source_root" if side=="source" else "destination_root"])
        rel=relative_path(data.get("relative",""));path=within(root,rel,existing=(side=="source"))
        dirs=[];skipped=0
        if path.exists():
            with os.scandir(path) as entries:
                for entry in entries:
                    if excluded_path(entry.name) or entry.is_symlink():skipped+=1;continue
                    if entry.is_dir(follow_symlinks=False):
                        child=entry.name if not rel else rel+"/"+entry.name
                        dirs.append({"name":entry.name,"relative":child})
        dirs.sort(key=lambda x:x["name"].casefold())
        display=region["source_display" if side=="source" else "destination_display"]
        return {"relative":rel,"container_path":str(path),"display_path":display.rstrip("/")+("/"+rel if rel else ""),"exists":path.exists(),"directories":dirs,"skipped":skipped}
    def selection(self,data):
        if not isinstance(data,dict) or set(data)-{"region_id","source_relative","dest_relative","name_bytes","settle_seconds","write_nfo","use_downloader"}:
            raise ValueError("任务选择参数无效")
        rid=data.get("region_id");region=self.get_region(rid)
        selection={"region_id":rid,"source_relative":relative_path(data.get("source_relative","")),
                   "dest_relative":relative_path(data.get("dest_relative","")),"name_bytes":data.get("name_bytes",240)}
        validate_budget(selection["name_bytes"])
        selection.update(settle_seconds=data.get("settle_seconds",0),write_nfo=data.get("write_nfo",False))
        validate_options(selection["settle_seconds"],selection["write_nfo"])
        if selection["write_nfo"] and not self.metadata_enabled:raise ValueError("NAS 已关闭元数据写入，请取消新增 NFO；由外部刮削器维护资料")
        use=data.get("use_downloader",False)
        if type(use) is not bool:raise ValueError("下载器检查选项必须为布尔值")
        selection["use_downloader"]=use or bool(region.get("require_downloader",False))
        source=within(Path(region["source_root"]),selection["source_relative"],True)
        dest=within(Path(region["destination_root"]),selection["dest_relative"])
        return region,selection,source,dest
    def verify(self,data):
        if not isinstance(data,dict) or set(data)!={"selection","source_relative","dest_relative"}:
            raise ValueError("核验参数无效")
        region,selection,source,dest=self.selection(data["selection"])
        if not self.operation_lock.acquire(blocking=False):raise RuntimeError("任务正在执行，请结束后核验")
        try:
            result={"checked_at":time.time(),"same_file":False,"shared_data":False,"source":None,"destination":None}
            for key,root,relative in (("source",source,data["source_relative"]),("destination",dest,data["dest_relative"])):
                relative_path(relative)
                if not relative:raise ValueError("请选择普通文件")
                path=root/relative
                boundary=Path(region["source_root"] if key=="source" else region["destination_root"])
                parent=path.parent.relative_to(boundary).as_posix()
                within(boundary,"" if parent=="." else parent)
                entry={"path":str(path),"exists":False}
                try:
                    info=path.lstat()
                    if not stat.S_ISREG(info.st_mode):raise ValueError("仅核验普通文件，不允许软链接或目录")
                    entry.update(exists=True,device=info.st_dev,inode=info.st_ino,links=info.st_nlink,size=info.st_size,mtime_ns=info.st_mtime_ns)
                except FileNotFoundError:pass
                result[key]=entry
            a=result["source"];b=result["destination"]
            if a["exists"] and b["exists"]:
                # Re-read both identities to detect replacement during the check.
                for entry in (a,b):
                    latest=Path(entry["path"]).lstat()
                    if not stat.S_ISREG(latest.st_mode) or (latest.st_dev,latest.st_ino,latest.st_size,latest.st_mtime_ns)!=(entry["device"],entry["inode"],entry["size"],entry["mtime_ns"]):
                        raise RuntimeError("核验期间文件发生变化，请重试")
                result["same_file"]=(a["device"],a["inode"])==(b["device"],b["inode"])
                result["shared_data"]=result["same_file"] and min(a["links"],b["links"])>=2
            result["source_display"]=region["source_display"].rstrip("/")+"/"+"/".join(filter(None,(selection["source_relative"],data["source_relative"])))
            result["destination_display"]=region["destination_display"].rstrip("/")+"/"+"/".join(filter(None,(selection["dest_relative"],data["dest_relative"])))
            return result
        finally:self.operation_lock.release()
    def preview_selection(self,data):
        region,selection,source,dest=self.selection(data)
        engine=self.make_engine(region,selection,source,dest)
        engine.lock=self.operation_lock;engine.selection_lock=threading.Lock();engine.event_callback=self.record_job
        engine.job_context={"region_id":region["id"],"region_name":region["name"],"selection":selection}
        engine.observations=self.observations;engine.cancel_events=self.cancel_events
        plan=engine.preview(selection["name_bytes"],selection["settle_seconds"],selection["write_nfo"]);plan["selection"]=selection
        plan["region_name"]=region["name"]
        now=time.monotonic()
        with self.meta_lock:
            self.plan_engines={pid:e for pid,e in self.plan_engines.items() if pid in e.plans and now-e.plans[pid]["created_at"]<1800}
            if len(self.plan_engines)>=4:self.plan_engines.pop(next(iter(self.plan_engines)))
            engine.plans[plan["plan_id"]]["selection"]=selection
            self.plan_engines[plan["plan_id"]]=engine
        return plan
    def start_selection(self,data):
        if not isinstance(data,dict) or set(data)-{"plan_id","confirmed","selection","task_name"}:raise ValueError("执行参数无效")
        pid=data.get("plan_id")
        with self.meta_lock:engine=self.plan_engines.get(pid)
        if not engine or pid not in engine.plans:raise ValueError("预览已失效，请重新扫描")
        with engine.selection_lock:
            if pid not in engine.plans:raise ValueError('预览已经执行或失效，请重新扫描')
            expected=engine.plans[pid].get("selection")
            _,selected,_,_=self.selection(data.get("selection"))
            if expected!=selected:raise ValueError("目录或规则已变化，旧预览不可执行")
            name=data.get("task_name","")
            if not isinstance(name,str) or len(name)>80:raise ValueError("任务名称过长")
            engine.job_context["task_name"]=name
            job=engine.start(pid,data.get("confirmed"))
            with self.meta_lock:self.plan_engines.pop(pid,None)
            return job
    def stop_job(self,data):
        if not isinstance(data,dict) or set(data)!={"job_id","confirmed"} or data["confirmed"]!="STOP_AFTER_CURRENT":
            raise ValueError("需要明确确认：当前文件完成后停止")
        job=self.job(data["job_id"])
        if job.get("done"):return {"accepted":False,"job":job,"message":"任务已结束，没有改变任何文件"}
        event=self.cancel_events.get(data["job_id"])
        if event is None:raise ValueError("无法找到运行中的任务，请恢复查看并只读核对")
        event.set()
        return {"accepted":True,"job":self.job(data["job_id"]),"message":"已请求停止；当前文件完成后不再处理下一项"}
    def recover_job(self,data):
        if not isinstance(data,dict) or set(data)-{"job_id","preview_id"}:raise ValueError("恢复参数无效")
        if data.get("job_id"):
            try:return {"found":True,"job":self.job(data["job_id"])}
            except ValueError:pass
        if data.get("preview_id"):
            for job in self.history()["jobs"]:
                if job.get("preview_id")==data["preview_id"]:return {"found":True,"job":job}
        return {"found":False,"message":"未查到任务记录，不能判断提交是否到达 NAS。请只读扫描核对，恢复查看不会重新创建。"}
    def record_job(self,job):
        # Only confirmed start/completion writes records, never browsing or preview.
        ensure_tree(self.state,self.state)
        with self.meta_lock:
            previous=self.jobs.get(job["job_id"])
            checkpoint=getattr(self,"checkpoint_times",{}).get(job["job_id"],(0,-100))
            if not hasattr(self,"checkpoint_times"):self.checkpoint_times={}
        now=time.monotonic();processed=job.get("processed",0)
        if previous is None or job.get("done") or now-checkpoint[0]>=1 or processed-checkpoint[1]>=100:
            event={k:v for k,v in job.items()}
            append_journal(self.state,event,filename="history.jsonl")
            self.checkpoint_times[job["job_id"]]=(now,processed)
        with self.meta_lock:self.jobs[job["job_id"]]=job
    def job(self,jid):
        with self.meta_lock:found=self.jobs.get(jid)
        if found:
            result=dict(found);event=self.cancel_events.get(jid)
            if event and event.is_set():result["stop_requested"]=True
            result["result_class"],result["result_label"]=classify(result)
            return result
        for record in self.history()["jobs"]:
            if record.get("job_id")==jid:return record
        raise ValueError("未找到任务；请重新扫描确认实际状态")
    def history(self):
        path=self.state/"history.jsonl"
        if not self.state.exists():return {"jobs":[],"warnings":[]}
        safe_directory(self.state)
        if not path.exists() and not path.is_symlink():return {"jobs":[],"warnings":[]}
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError("历史文件不能是链接")
        records={};warnings=[]
        with private_reader(path) as stream:
            for number,line in enumerate(stream,1):
                if not line.strip():continue
                try:
                    record=json.loads(line);records[record["job_id"]]=record
                except (ValueError,KeyError,TypeError):warnings.append("历史第"+str(number)+"行不完整，已跳过")
        with self.meta_lock:
            for jid,record in self.jobs.items():records[jid]=dict(record)
        for jid,record in records.items():
            if not record.get("done") and jid not in self.jobs:
                record["status"]="unconfirmed";record["error"]="执行端曾重启，完成状态未确认，请重新扫描"
                record["done"]=True
            event=self.cancel_events.get(jid)
            if event and event.is_set():record["stop_requested"]=True
            record["result_class"],record["result_label"]=classify(record)
        return {"jobs":sorted(records.values(),key=lambda r:r.get("started_at",0),reverse=True)[:200],"warnings":warnings}
