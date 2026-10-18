"""Secret-free saved tasks and testable preview invalidation."""
import json
import os
from pathlib import Path

OPTIONAL={"settle_seconds":300,"write_nfo":False,"key_file":"","use_downloader":False}
FIELDS=("url","region_id","source_relative","dest_relative","name_bytes")
class TaskStore:
    def __init__(self,directory):
        self.path=Path(directory)/"tasks.json"
    def load(self):
        if not self.path.exists():return {}
        try:
            data=json.loads(self.path.read_text("utf-8"))
            if not isinstance(data,dict):raise ValueError()
            return {name:{**{k:task[k] for k in FIELDS},**{k:task.get(k,v) for k,v in OPTIONAL.items()}} for name,task in data.items() if isinstance(name,str) and isinstance(task,dict) and all(k in task for k in FIELDS)}
        except (ValueError,TypeError,KeyError) as e:raise ValueError("任务配置文件损坏，请先备份后检查，不会自动覆盖。") from e
    def save(self,name,task):
        name=name.strip()
        if not name or len(name)>80:raise ValueError("任务名称应为1～80个字符")
        selected={**{k:task[k] for k in FIELDS},**{k:task.get(k,v) for k,v in OPTIONAL.items()}}
        from media_features import validate_options
        validate_options(selected["settle_seconds"],selected["write_nfo"])
        budget=selected["name_bytes"]
        if type(budget) is not int or not 80<=budget<=240:raise ValueError("名称上限必须为80～240")
        tasks=self.load();tasks[name]=selected
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(tasks,ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(temp,self.path)
        return tasks

def choice_key(url,token,selection):
    return (url,token,json.dumps(selection,sort_keys=True,ensure_ascii=False))

class PreviewState:
    def __init__(self):self.plan=None;self.key=None
    def invalidate(self):self.plan=None;self.key=None
    def accept(self,plan,key,current_key):
        if key!=current_key:return False
        self.plan=plan;self.key=key;return True
    def executable(self,current_key):
        return bool(self.plan and self.key==current_key and not self.plan["errors"] and any(r["status"]=="待创建" for r in self.plan["rows"]))
