"""Fixed-root NAS executor; no shell, arbitrary path, copying or deletion API."""
import errno,hmac,json,os,secrets,stat,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from link_rules import VERSION,excluded_path,target_relative,legacy_target_relative,previous_target_relative,paired_target_relative,validate_budget
from safe_fs import link as pinned_link,ensure_tree,open_private,private_reader,physical_overlap
from job_status import classify
from media_features import temporary,readiness,nfo_rows,title_xml,validate_options

def native_hardlink(src,dest):
    # Parent st_dev is advisory on virtual filesystems; the syscall decides.
    try:
        return pinned_link(src,dest)
    except OSError as error:
        if error.errno==errno.EXDEV:
            raise RuntimeError(f'系统实际拒绝跨文件系统硬链接（EXDEV）。未复制文件。\n原文件：{src}\n目标：{dest}') from error
        raise OSError(f'系统创建硬链接失败，未转为复制。\n原文件：{src}\n目标：{dest}\n{error}') from error

def snapshot(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
def safe_directory(path,create=False):
    path=Path(path)
    if not path.is_absolute():raise ValueError("Directory must be absolute")
    current=Path(path.anchor)
    for part in path.parts[1:]:
        if part in (".",".."):raise ValueError("Unsafe path")
        current=current/part
        try:info=current.lstat()
        except FileNotFoundError:
            if not create:raise
            current.mkdir();info=current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Symlink or non-directory: "+str(current))
    return path
def require_inside(root,path):
    root=Path(root);path=Path(path)
    if not path.is_absolute() or '..' in path.parts or path==root or not path.is_relative_to(root):
        raise ValueError('Path outside configured directory: '+str(path))
    return path

def ensure_destination(root,parent):
    root=Path(root);parent=Path(parent)
    if '..' in parent.parts or (parent!=root and not parent.is_relative_to(root)):
        raise ValueError('Cannot create directory outside destination: '+str(parent))
    ensure_tree(root,parent)

def check_existing_directory(path):
    path=Path(path)
    current=Path(path.anchor)
    for part in path.parts[1:]:
        if part in ('.','..'):raise ValueError('Unsafe directory')
        current=current/part
        try:info=current.lstat()
        except FileNotFoundError:continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):raise ValueError('Symlink or non-directory: '+str(current))

def append_journal(state,record,filename='created.jsonl'):
    if filename not in ('created.jsonl','history.jsonl'):raise ValueError('Invalid journal name')
    path=Path(state)/filename
    if path.exists() or path.is_symlink():
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('Journal must be a private regular file, not a link')
    flags=os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
    fd=open_private(path,flags,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('Refusing to write shared journal file')
        with os.fdopen(fd,'a',encoding='utf-8',closefd=False) as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')
    finally:os.close(fd)

class Executor:
    def __init__(self,source,destination,state,destination_boundary=None):
        self.source=Path(os.path.abspath(source));self.destination=Path(os.path.abspath(destination));self.state=Path(os.path.abspath(state))
        self.destination_boundary=Path(os.path.abspath(destination_boundary or destination))
        if self.destination!=self.destination_boundary and not self.destination.is_relative_to(self.destination_boundary):raise ValueError('Destination outside allowed boundary')
        if self.source==self.destination_boundary or self.source in self.destination_boundary.parents or self.destination_boundary in self.source.parents:raise ValueError('Output boundary overlaps source')
        if self.source==self.destination or self.source in self.destination.parents or self.destination in self.source.parents:
            raise ValueError("Source and destination cannot overlap")
        if excluded_path(self.source.as_posix()) or excluded_path(self.destination.as_posix()):raise ValueError("Recycle root")
        for media in (self.source,self.destination_boundary):
            if self.state==media or self.state in media.parents or media in self.state.parents:
                raise ValueError('State directory must be separate from media roots')
        safe_directory(self.source)
        safe_directory(self.state.parent)
        safe_directory(self.destination_boundary.parent)
        check_existing_directory(self.destination)
        if self.destination.exists() or self.destination.is_symlink():safe_directory(self.destination)
        if physical_overlap(self.source,self.destination_boundary) or any(physical_overlap(self.state,m) for m in (self.source,self.destination_boundary)):raise ValueError("实际目录身份重叠，拒绝创建")
        self.source_identity=(self.source.stat().st_dev,self.source.stat().st_ino)
        self.plans={};self.jobs={};self.lock=threading.Lock();self.event_callback=None;self.job_context={};self.observations={};self.cancel_events={};self.downloader=None;self.cancel_check=lambda:False;self.record_warnings=[]
    def recorded_entries(self):
        journal=self.state/'created.jsonl'
        if not self.state.exists():return {}
        safe_directory(self.state)
        if not journal.exists() and not journal.is_symlink():return {}
        info=journal.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('Journal must not be a symlink or hardlink')
        entries={}
        with private_reader(journal) as stream:
            for number,line in enumerate(stream,1):
                if not line.strip():continue
                try:record=json.loads(line)
                except ValueError:
                    if not line.endswith("\n"):
                        self.record_warnings.append("创建记录末行不完整，未采用该行；请保留日志并只读复核。");continue
                    raise ValueError("创建记录第"+str(number)+"行损坏，已阻止创建，请检查日志")
                if not isinstance(record,dict):raise ValueError("创建记录格式错误")
                if record.get('action') in ('nfo','nfo_update'):continue
                src=Path(record['source']);dest=Path(record['dest'])
                # A state volume may contain records from previously configured roots.
                if '..' in src.parts or '..' in dest.parts:continue
                if src.is_relative_to(self.source) and dest.is_relative_to(self.destination):
                    entries.setdefault(str(src),[]).append(dest)
        return entries
    def preview(self,name_bytes=240,settle_seconds=0,write_nfo=False):
        validate_budget(name_bytes);validate_options(settle_seconds,write_nfo)
        if not self.lock.acquire(blocking=False):raise RuntimeError("Operation in progress")
        try:
            safe_directory(self.source)
            if self.destination.exists() or self.destination.is_symlink():safe_directory(self.destination)
            if self.downloader:self.downloader.refresh()
            rows=[];errors=[];seen=set();skipped=0;skipped_entries=[];recorded=self.recorded_entries()
            def visit(directory):
                nonlocal skipped
                if self.cancel_check():return
                try:
                    safe_directory(directory)
                    with os.scandir(directory) as iterator:entries=list(iterator)
                    deferred=readiness(directory,settle_seconds,self.observations,entries)
                    extensions={}
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False) and not temporary(entry.name):
                            stem,ext=os.path.splitext(entry.name);extensions.setdefault(stem,[]).append(ext)
                    for e in entries:
                        if self.cancel_check():break
                        rel=Path(e.path).relative_to(self.source).as_posix();file_deferred=deferred
                        if excluded_path(rel) or e.is_symlink():
                            skipped+=1;skipped_entries.append({'rel':rel,'reason':'回收站，未进入目录' if excluded_path(rel) else '软链接，未跟随'})
                            continue
                        if temporary(e.name):
                            skipped+=1;skipped_entries.append({"rel":rel,"reason":"下载临时文件或目录，不进入"});continue
                        if e.is_dir(follow_symlinks=False):visit(Path(e.path));continue
                        if not e.is_file(follow_symlinks=False):skipped+=1;skipped_entries.append({"rel":rel,"reason":"非普通文件"});continue
                        info=Path(e.path).lstat();newrel=paired_target_relative(rel,name_bytes,extensions.get(os.path.splitext(e.name)[0],[]));dest=self.destination/newrel
                        candidates=[dest,self.destination/previous_target_relative(rel,name_bytes),self.destination/legacy_target_relative(rel)]+recorded.get(e.path,[])
                        for candidate in candidates:
                            require_inside(self.destination,candidate)
                            if candidate.exists() or candidate.is_symlink():
                                safe_directory(candidate.parent)
                                oldinfo=candidate.lstat()
                                if stat.S_ISREG(oldinfo.st_mode) and os.path.samestat(info,oldinfo):
                                    dest=candidate;newrel=dest.relative_to(self.destination).as_posix();break
                        if newrel in seen:raise RuntimeError("Mapping collision: "+newrel)
                        seen.add(newrel);status="待创建";reason="名称保持原样" if newrel==rel else "按命名规则生成入口"
                        if dest.exists() or dest.is_symlink():
                            d=dest.lstat();status="已有硬链接" if stat.S_ISREG(d.st_mode) and os.path.samestat(info,d) else "目标冲突"
                            reason='设备号与文件编号相同，跳过已有入口' if status=='已有硬链接' else ('目标为软链接，不覆盖' if stat.S_ISLNK(d.st_mode) else '目标已存在且不是同一文件，不覆盖')
                        if self.downloader:
                            ready,download_note=self.downloader.check(e.path,info.st_size)
                            reason+="；"+download_note
                            if not ready:file_deferred=download_note
                        if status=="待创建" and file_deferred:status="暂缓处理";reason=file_deferred
                        if len(rows)>=200000:raise RuntimeError("预览超过20万文件，请缩小选择范围后扫描")
                        rows.append(dict(action="hardlink",deferred_reason=file_deferred,status=status,reason=reason,source=e.path,dest=str(dest),rel=rel,newrel=newrel,bytes=len(e.name.encode()),snapshot=snapshot(info)))
                except Exception as e:errors.append(str(directory)+": "+str(e))
            visit(self.source)
            groups={}
            for row in rows:groups.setdefault((str(Path(row["source"]).parent),Path(row["source"]).stem),[]).append(row)
            for group in groups.values():
                existing={Path(row["dest"]).stem for row in group if row["status"]=="已有硬链接"}
                for row in group:
                    if row["status"]=="待创建" and existing and Path(row["dest"]).stem not in existing:
                        row["status"]="目标冲突";row["reason"]="既有视频/配套文件主名与新规则不同；保留旧入口，请人工核对或使用新入口目录"
            if write_nfo:
                for row in rows:check_existing_directory(Path(row["dest"]).parent)
                rows.extend(nfo_rows(rows,self.destination))
            rows.sort(key=lambda r:(r.get("action")=="nfo",r["rel"]))
            now=time.monotonic();self.plans={k:v for k,v in self.plans.items() if now-v["created_at"]<1800}
            if len(self.plans)>=4:self.plans.pop(next(iter(self.plans)))
            for index,row in enumerate(rows):row["row_id"]=str(index)
            pid=secrets.token_urlsafe(24)
            plan=dict(plan_id=pid,version=VERSION,warnings=list(self.record_warnings),source=str(self.source),dest=str(self.destination),rows=rows,errors=errors,skipped=skipped,skipped_entries=skipped_entries,name_bytes=name_bytes,settle_seconds=settle_seconds,write_nfo=write_nfo,created_at=now)
            self.plans[pid]=plan
            return {k:v for k,v in plan.items() if k!="created_at"}
        finally:self.lock.release()
    def start(self,plan_id,confirmed):
        if confirmed not in ("CREATE_HARDLINKS","UPDATE_NFO_TITLES"):raise ValueError("Explicit confirmation required")
        if not self.lock.acquire(blocking=False):raise RuntimeError("Operation in progress")
        try:
            plan=self.plans.get(plan_id)
            if not plan or time.monotonic()-plan["created_at"]>1800:raise ValueError("Preview expired; scan again")
            if confirmed!=plan.get("confirmation","CREATE_HARDLINKS"):raise ValueError("预览类型与确认操作不一致")
            if plan["errors"]:raise ValueError("Scan errors; creation blocked")
            pending=[r for r in plan["rows"] if r["status"]=="待创建"]
            if not pending:raise ValueError("No pending files")
            self.job_context["settle_seconds"]=plan.get("settle_seconds",0)
            self.plans.pop(plan_id);jid=secrets.token_urlsafe(24)
            job=dict(job_id=jid,preview_id=plan_id,total=len(pending),created=0,metadata_started=0,current_file="",current_action="",stop_requested=False,metadata_created=0,metadata_updated=0,processed=0,done=False,error="",status="running",started_at=time.time(),finished_at=None,source=str(self.source),dest=str(self.destination),**self.job_context);self.jobs[jid]=job
            self.cancel_events[jid]=threading.Event()
            if self.event_callback:self.event_callback(job)
            threading.Thread(target=self._execute,args=(pending,job),daemon=True).start()
            return dict(job)
        except Exception:
            if "jid" in locals():self.cancel_events.pop(jid,None)
            self.lock.release();raise
    def publish(self,job):
        job["updated_at"]=time.time()
        job["result_class"],job["result_label"]=classify(job)
        if self.event_callback:self.event_callback(job)
    def _execute(self,rows,job):
        try:
            safe_directory(self.source)
            if (self.source.stat().st_dev,self.source.stat().st_ino)!=self.source_identity:raise ValueError("原件根目录身份已变化，请重新预览")
            if physical_overlap(self.source,self.destination_boundary) or any(physical_overlap(self.state,m) for m in (self.source,self.destination_boundary)):raise ValueError("执行前发现实际目录重叠")
            ensure_tree(self.state,self.state)
            readiness_cache={}
            if self.downloader:self.downloader.refresh()
            for r in rows:
                if self.cancel_events[job["job_id"]].is_set():
                    job["stop_requested"]=True
                    break
                job["current_file"]=r["rel"];job["current_action"]=r.get("action","hardlink")
                self.publish(job)
                src=Path(r["source"]);dest=Path(r["dest"])
                require_inside(self.source,src);require_inside(self.destination,dest)
                if excluded_path(src.as_posix()) or excluded_path(dest.as_posix()):raise ValueError("Recycle path")
                safe_directory(src.parent);info=src.lstat()
                if not stat.S_ISREG(info.st_mode) or tuple(r["snapshot"])!=snapshot(info):raise RuntimeError("Source changed: "+str(src))
                checked=readiness_cache.get(src.parent,0)
                if time.monotonic()-checked>=2:
                    if readiness(src.parent,self.job_context.get("settle_seconds",0),self.observations):raise RuntimeError("文件组仍在下载或变化，已停止；请静置后重新扫描")
                    readiness_cache[src.parent]=time.monotonic()
                if self.downloader:
                    ready,note=self.downloader.check(src,info.st_size)
                    if not ready:raise RuntimeError(note)
                if r.get("action")=="nfo_update":
                    from nfo_editor import update
                    media=Path(r["media_dest"]);safe_directory(media.parent)
                    linked=media.lstat()
                    if not stat.S_ISREG(linked.st_mode) or not os.path.samestat(info,linked):raise RuntimeError("视频入口身份变化，拒绝更新 NFO")
                    backup=update(dest,r,self.state,job)
                    job["metadata_updated"]+=1;job["processed"]+=1
                    append_journal(self.state,dict(action="nfo_update",source=str(src),dest=str(dest),backup=backup,time=time.time(),job_id=job["job_id"]))
                    self.publish(job)
                    continue
                ensure_destination(self.destination_boundary,dest.parent)
                if r.get("action")=="nfo":
                    media=Path(r["media_dest"]);safe_directory(media.parent)
                    linked=media.lstat()
                    if not stat.S_ISREG(linked.st_mode) or not os.path.samestat(info,linked):raise RuntimeError("视频入口身份已变化，不生成 NFO")
                    payload=title_xml(r["title"])
                    fd=open_private(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
                    job["metadata_started"]+=1
                    try:
                        with os.fdopen(fd,"wb",closefd=False) as output:output.write(payload);output.flush();os.fsync(fd)
                    finally:os.close(fd)
                    job["metadata_created"]+=1;job["metadata_started"]-=1;job["processed"]+=1
                    append_journal(self.state,dict(source=str(src),dest=str(dest),action="nfo",time=time.time(),job_id=job["job_id"]))
                    self.publish(job)
                    continue
                try:linked_info=native_hardlink(src,dest)
                except Exception as error:
                    if getattr(error,"created",False):job["created"]+=1
                    raise
                job["created"]+=1;new=linked_info or dest.lstat()
                if snapshot(src.lstat())!=snapshot(info):raise RuntimeError("入口可能已创建，但原件在创建期间发生变化，请只读复核")
                if not stat.S_ISREG(new.st_mode) or not os.path.samestat(info,new) or new.st_nlink<2:raise RuntimeError(f"入口已创建但身份核验未通过，请勿重复执行。源 dev/inode={info.st_dev}/{info.st_ino}，目标 dev/inode={new.st_dev}/{new.st_ino}，目标链接数={new.st_nlink}：{dest}")
                job["processed"]+=1
                append_journal(self.state,dict(source=str(src),dest=str(dest),device=new.st_dev,inode=new.st_ino,time=time.time(),job_id=job["job_id"]))
                self.publish(job)
        except Exception as e:job["error"]=str(e)
        finally:
            job['done']=True;job['finished_at']=time.time();job['status']='failed' if job['error'] else ('stopped' if job['stop_requested'] and job['processed']<job['total'] else 'completed')
            job['current_file']='';job['current_action']=''
            try:
                self.publish(job)
            except Exception as history_error:
                job['error']=(job['error']+'\n' if job['error'] else '')+'历史记录保存失败：'+str(history_error)
                job['status']='failed';job['result_class'],job['result_label']=classify(job)
            finally:
                self.cancel_events.pop(job['job_id'],None)
                self.lock.release()
    def job(self,jid):
        if jid not in self.jobs:raise ValueError("Unknown job")
        return dict(self.jobs[jid])
def make_server(executor,token,address=("0.0.0.0",8787)):
    if not 24<=len(token)<=512 or not token.isascii() or any(ord(c)<33 or ord(c)>126 for c in token) or token.startswith("REPLACE"):raise ValueError("Set AGENT_TOKEN: at least 24 random characters")
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(20)
        def log_message(self,*args):pass
        def reply(self,code,data):
            body=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body)
        def authorized(self):return hmac.compare_digest(self.headers.get("Authorization","").encode(),("Bearer "+token).encode())
        def do_GET(self):
            if not self.authorized():self.reply(401,dict(error="连接密钥错误"));return
            try:
                if self.path=="/health":self.reply(200,executor.health() if hasattr(executor,"health") else dict(version=VERSION,source=str(executor.source),dest=str(executor.destination),backend="NAS-local os.link",delete_guard=getattr(executor,"delete_guard",{"active":False})))
                elif self.path=="/regions" and hasattr(executor,"region_list"):self.reply(200,executor.region_list())
                elif self.path=="/history" and hasattr(executor,"history"):self.reply(200,executor.history())
                elif self.path.startswith("/jobs/"):self.reply(200,executor.job(self.path.split("/")[-1]))
                else:self.reply(404,dict(error="Not found"))
            except Exception as e:self.reply(400,dict(error=str(e)))
        def do_POST(self):
            if not self.authorized():self.reply(401,dict(error="连接密钥错误"));return
            try:
                length=int(self.headers.get("Content-Length","0"))
                if length<0 or length>16384:raise ValueError("Request too large")
                data=json.loads(self.rfile.read(length) or b"{}")
                if self.path=="/preview":
                    if hasattr(executor,'preview_selection'):self.reply(200,executor.preview_selection(data))
                    else:
                        if not isinstance(data,dict) or set(data)-{"name_bytes"}:raise ValueError("Client cannot change configured directories")
                        self.reply(200,executor.preview(data.get("name_bytes",240)))
                elif self.path=="/audit/start" and hasattr(executor,"audit_start"):self.reply(200,executor.audit_start(data))
                elif self.path=="/audit/page" and hasattr(executor,"audit_page"):self.reply(200,executor.audit_page(data))
                elif self.path=="/audit/stop" and hasattr(executor,"audit_stop"):self.reply(200,executor.audit_stop(data))
                elif self.path=="/metadata/preview" and hasattr(executor,"metadata_preview"):self.reply(200,executor.metadata_preview(data))
                elif self.path=="/metadata/execute" and hasattr(executor,"metadata_execute"):self.reply(200,executor.metadata_execute(data))
                elif self.path=="/verify" and hasattr(executor,"verify"):self.reply(200,executor.verify(data))
                elif self.path=="/browse" and hasattr(executor,'browse'):self.reply(200,executor.browse(data))
                elif self.path=="/stop" and hasattr(executor,"stop_job"):self.reply(200,executor.stop_job(data))
                elif self.path=="/recover" and hasattr(executor,"recover_job"):self.reply(200,executor.recover_job(data))
                elif self.path=="/execute":
                    self.reply(200,executor.start_selection(data) if hasattr(executor,'start_selection') else executor.start(data.get("plan_id"),data.get("confirmed")))
                else:self.reply(404,dict(error="Not found"))
            except Exception as e:self.reply(400,dict(error=str(e)))
    class BoundedServer(ThreadingHTTPServer):
        daemon_threads=True
        def __init__(self,*args,**kwargs):
            self.slots=threading.BoundedSemaphore(32);super().__init__(*args,**kwargs)
        def process_request(self,request,client_address):
            if not self.slots.acquire(blocking=False):
                try:request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                finally:self.shutdown_request(request)
                return
            try:super().process_request(request,client_address)
            except Exception:self.slots.release();raise
        def process_request_thread(self,request,client_address):
            try:super().process_request_thread(request,client_address)
            finally:self.slots.release()
    return BoundedServer(address,Handler)
if __name__=="__main__":
    from delete_guard import enforce_no_delete
    protection=enforce_no_delete()
    from region_service import RegionService
    ex=RegionService.from_environment(protection)
    server=make_server(ex,os.environ.get("AGENT_TOKEN",""),("0.0.0.0",int(os.environ.get("PORT","8787"))))
    print("EmbyLinkStudio agent "+VERSION+" ready; fixed roots only; no-delete protection active.",flush=True);server.serve_forever()
