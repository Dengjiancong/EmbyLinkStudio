"""Local submission receipts, deliberately excluding tokens and key contents."""
import json,os,time
from pathlib import Path
class RecoveryStore:
    def __init__(self,directory):self.path=Path(directory)/"recovery.json"
    def load(self):
        if not self.path.exists():return []
        try:
            value=json.loads(self.path.read_text("utf-8"))
            if not isinstance(value,list):raise ValueError()
            return [{k:r.get(k,"") for k in ("url","preview_id","job_id","task_name","saved_at")} for r in value if isinstance(r,dict) and isinstance(r.get("url"),str) and isinstance(r.get("preview_id"),str)]
        except (OSError,ValueError,TypeError):raise ValueError("本地恢复记录无法读取，请备份 recovery.json 后检查；不会自动覆盖。") from None
    def remember(self,url,preview_id,job_id="",task_name=""):
        rows=self.load()
        old=next((r for r in rows if r["url"]==url and r["preview_id"]==preview_id),{})
        row=dict(url=url,preview_id=preview_id,job_id=job_id or old.get("job_id",""),task_name=task_name or old.get("task_name",""),saved_at=time.time())
        rows=[r for r in rows if not (r["url"]==url and r["preview_id"]==preview_id)];rows.append(row)
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows[-200:],ensure_ascii=False,indent=2),encoding="utf-8");os.replace(temp,self.path)
        return row
    def latest(self,url):return next((r for r in reversed(self.load()) if r["url"]==url),None)
