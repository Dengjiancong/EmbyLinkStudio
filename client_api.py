import json
import urllib.error,urllib.parse,urllib.request

class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class AgentClient:
    def __init__(self,url,token):
        self.url=url.rstrip("/");p=urllib.parse.urlsplit(self.url)
        if p.scheme not in ("http","https") or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ("","/"):
            raise ValueError("请填写 http://NAS局域网IP:8787，不包含用户名或密码")
        if not token:raise ValueError("请填写 Docker 执行端的连接密钥")
        self.token=token
    def call(self,path,data=None):
        request=urllib.request.Request(self.url+path,data=None if data is None else json.dumps(data).encode(),headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoCredentialRedirect()).open(request,timeout=120) as response:return json.load(response)
        except urllib.error.HTTPError as e:
            try:detail="执行端返回重定向，请填写最终地址；连接密钥未转发。" if 300<=e.code<400 else json.loads(e.read()).get("error",str(e))
            except Exception:detail=str(e)
            finally:e.close()
            raise RuntimeError(detail) from e
        except urllib.error.URLError as e:raise RuntimeError("无法连接 NAS 执行端："+str(e.reason)) from e
        except (OSError, TimeoutError) as e:raise RuntimeError("NAS connection interrupted; check connection and recover task status before retrying: "+str(e)) from e
