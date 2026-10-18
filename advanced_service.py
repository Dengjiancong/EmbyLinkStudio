"""Paged, read-only audit jobs and separately confirmed NFO title maintenance."""
import collections,os,secrets,stat,threading,time
from pathlib import Path
from nas_agent import Executor,safe_directory,check_existing_directory,snapshot
from media_features import VIDEO_EXTS
from nfo_editor import inspect
class AdvancedService:
    def make_engine(self,region,selection,source,dest):
        engine=Executor(source,dest,self.state,destination_boundary=region["destination_root"])
        engine.observations=self.observations;engine.cancel_events=self.cancel_events
        engine.event_callback=self.record_job
        engine.job_context={"region_id":region["id"],"region_name":region["name"],"selection":selection}
        engine.downloader=self.downloader_for(region,selection)
        return engine
    def metadata_preview(self,data):
        if not self.metadata_enabled:raise ValueError("NAS 已关闭元数据写入，当前专注硬链接创建与核验")
        region,selection,source,dest=self.selection(data)
        if not self.operation_lock.acquire(blocking=False):raise RuntimeError("其他任务正在执行")
        try:
            engine=self.make_engine(region,selection,source,dest)
            plan=engine.preview(selection["name_bytes"],selection["settle_seconds"],False)
            videos=[r for r in plan["rows"] if Path(r["source"]).suffix.lower() in VIDEO_EXTS and r["status"]=="已有硬链接"]
            counts=collections.Counter(str(Path(r["dest"]).with_suffix(".nfo")).casefold() for r in videos)
            rows=[];visited=set();names={}
            for video in videos:
                path=Path(video["dest"]).with_suffix(".nfo");key=str(path).casefold()
                if key in visited:continue
                visited.add(key);row={**video,"row_id":str(len(rows)),"action":"nfo_update","dest":str(path),"newrel":path.relative_to(dest).as_posix(),"new_title":Path(video["source"]).stem,"old_title":"","status":"不可更新","media_dest":video["dest"]}
                try:
                    check_existing_directory(path.parent)
                    if path.parent not in names:
                        names[path.parent]={p.name.casefold():p for p in path.parent.iterdir()}
                    path=names[path.parent].get(path.name.casefold(),path);row["dest"]=str(path);row["newrel"]=path.relative_to(dest).as_posix()
                    if counts[key]>1:raise ValueError("多个视频共用同主名 NFO，需人工确认")
                    if video.get("deferred_reason"):raise ValueError(video["deferred_reason"])
                    if not path.exists() and not path.is_symlink():raise ValueError("缺少 NFO：请使用普通预览的新增 NFO 功能")
                    row.update(inspect(path,row["new_title"]))
                except Exception as error:row["reason"]=str(error)
                rows.append(row)
            plan.update(rows=rows,selection=selection,region_name=region["name"],confirmation="UPDATE_NFO_TITLES",kind="metadata")
            engine.plans[plan["plan_id"]].update(plan)
            engine.job_context["kind"]="metadata";engine.lock=self.operation_lock;engine.selection_lock=threading.RLock()
            with self.meta_lock:
                if len(self.plan_engines)>=4:self.plan_engines.pop(next(iter(self.plan_engines)))
                self.plan_engines[plan["plan_id"]]=engine
            return plan
        finally:self.operation_lock.release()
    def metadata_execute(self,data):
        if not self.metadata_enabled:raise ValueError("NAS 已关闭元数据写入")
        if not isinstance(data,dict) or data.get("confirmed")!="UPDATE_NFO_TITLES":raise ValueError("必须明确确认修改 NFO 标题")
        if set(data)-{"plan_id","confirmed","selection","task_name","row_ids"}:raise ValueError("NFO 更新参数无效")
        with self.meta_lock:engine=self.plan_engines.get(data.get("plan_id"))
        if not engine:raise ValueError("NFO 预览已过期")
        with engine.selection_lock:
            plan=engine.plans.get(data["plan_id"])
            if not plan or plan.get("kind")!="metadata":raise ValueError("NFO 预览已过期或类型错误")
            selected=data.get("row_ids")
            eligible=[r for r in plan["rows"] if r["status"]=="待创建"]
            if selected=="ALL_ELIGIBLE":chosen=eligible
            elif isinstance(selected,list) and selected and all(isinstance(i,str) for i in selected):
                ids=set(selected);chosen=[r for r in eligible if r["row_id"] in ids]
                if len(chosen)!=len(ids):raise ValueError("选中项目包含不可更新或未知行")
            else:raise ValueError("请选择需要更新的 NFO")
            if not chosen:raise ValueError("没有可更新的 NFO")
            # Validate the bound selection before changing the single-use plan.
            _,normalized,_,_=self.selection(data.get("selection"))
            if normalized!=plan["selection"]:raise ValueError("设置已变化，需重新预览")
            previous=plan["rows"];plan["rows"]=chosen
            try:return self.start_selection({k:v for k,v in data.items() if k!="row_ids"})
            except Exception:
                plan["rows"]=previous;raise
    def audit_start(self,data):
        region,selection,source,dest=self.selection(data)
        if not self.operation_lock.acquire(blocking=False):raise RuntimeError("其他任务正在执行")
        aid=secrets.token_urlsafe(24)
        job=dict(audit_id=aid,done=False,phase="正在扫描目录",processed=0,total=0,counts={},rows=[],errors=[],checked_at=time.time(),cancelled=False)
        with self.meta_lock:
            self.audits.clear();self.audits[aid]=job
        def work():
            try:
                # Auditing identity never requires downloader login or creates plans for execution.
                engine=Executor(source,dest,self.state,destination_boundary=region["destination_root"])
                engine.observations=self.observations
                engine.cancel_check=lambda:job["cancelled"]
                plan=engine.preview(selection["name_bytes"],0,False)
                job["total"]=len(plan["rows"]);job["phase"]="核验文件身份"
                counts=collections.Counter()
                for row in plan["rows"]:
                    if job["cancelled"]:break
                    report=dict(source=row["source"],destination=row["dest"],relative=row["rel"],status="无法核验",reason="",device="",inode="",links="",size="",checked_at=time.time())
                    try:
                        check_existing_directory(Path(row["dest"]).parent)
                        a=Path(row["source"]).lstat();b=Path(row["dest"]).lstat()
                        if not stat.S_ISREG(a.st_mode) or not stat.S_ISREG(b.st_mode):report.update(status="冲突",reason="路径不是普通文件，或为软链接")
                        elif tuple(row["snapshot"])!=snapshot(a):report["reason"]="核验过程中原件发生变化"
                        elif os.path.samestat(a,b) and b.st_nlink>=2:report.update(status="正常",reason="设备号/文件编号相同，链接数至少为2")
                        else:report.update(status="冲突",reason="不是同一文件的硬链接")
                        report.update(device=b.st_dev,inode=b.st_ino,links=b.st_nlink,size=b.st_size)
                    except FileNotFoundError:report.update(status="缺失",reason="原件或预期入口不存在")
                    except Exception as error:report["reason"]=str(error)
                    job["rows"].append(report);counts[report["status"]]+=1
                    job["processed"]+=1;job["counts"]=dict(counts)
                for error in plan["errors"]:
                    job["rows"].append(dict(relative="",source="",destination="",status="无法核验",reason=error,checked_at=time.time()))
                    counts["无法核验"]+=1
                job["counts"]=dict(counts);job["skipped"]=plan["skipped_entries"]
            except Exception as error:job["errors"].append(str(error))
            finally:
                job["done"]=True;job["finished_at"]=time.time();job["phase"]="已停止（部分报告）" if job["cancelled"] else ("核验失败/不完整" if job["errors"] else "核验结束")
                self.operation_lock.release()
        try:threading.Thread(target=work,daemon=True).start()
        except Exception:self.operation_lock.release();raise
        return {k:v for k,v in job.items() if k!="rows"}
    def audit_page(self,data):
        aid=data.get("audit_id");offset=int(data.get("offset",0));limit=int(data.get("limit",500))
        if offset<0 or not 1<=limit<=500:raise ValueError("分页参数无效，单页最多500行")
        with self.meta_lock:job=self.audits.get(aid)
        if not job:raise ValueError("核验报告不存在或已失效，请重新核验")
        result={k:v for k,v in job.items() if k not in ("rows","skipped")}
        result.update(rows=list(job["rows"][offset:offset+limit]),offset=offset,row_count=len(job["rows"]),skipped_count=len(job.get("skipped",[])))
        return result
    def audit_stop(self,data):
        with self.meta_lock:job=self.audits.get(data.get("audit_id"))
        if not job:raise ValueError("未找到核验任务")
        job["cancelled"]=True
        return {"message":"已请求停止只读核验，已有报告保留"}
