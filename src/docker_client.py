"""Windows regional task UI; every media mutation requires a fresh confirmed plan."""
import csv,json,os,queue,sys,threading,time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from link_rules import VERSION
from client_api import AgentClient
from client_models import TaskStore,PreviewState,choice_key
from directory_picker import DirectoryPicker
from credentials import read_token_file
from recovery_store import RecoveryStore
from job_status import classify,summary
from advanced_ui import AdvancedUI
CLIENT_VERSION=VERSION
APP=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"EmbyLinkStudio"
def display_time(value):
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else "—"
class Studio(AdvancedUI):
    def __init__(self):
        self.root=tk.Tk();self.root.title("Emby 硬链接整理 v"+CLIENT_VERSION+" · 批量硬链接");self.root.geometry("1280x900");self.root.minsize(1050,760)
        self.q=queue.Queue();self.busy=False;self.preview=PreviewState();self.rows=[];self.skips=[];self.row_selection=None;self.row_connection=None
        self.regions={};self.connected=None;self.pending_task=None;self.store=TaskStore(APP);self.region_labels={};self.recovery=RecoveryStore(APP);self.active_receipt=None;self.monitor_client=None;self.monitoring=False;self.metadata_allowed=False;self.advanced_expanded=False;self.frozen_fields=[]
        settings={}
        try:settings=json.loads((APP/"agent_settings.json").read_text("utf-8"))
        except (OSError,ValueError):pass
        self.url=tk.StringVar(value=settings.get("url","http://127.0.0.1:8787"));self.token=tk.StringVar()
        self.key_file=tk.StringVar(value=settings.get("key_file",""));self.progress_text=tk.StringVar(value="尚无运行任务");self.progress_value=tk.DoubleVar(value=0)
        self.region_id=tk.StringVar();self.region_label=tk.StringVar();self.source_rel=tk.StringVar();self.dest_rel=tk.StringVar()
        self.settle=tk.StringVar(value=str(settings.get("settle_seconds",300)));self.write_nfo=tk.BooleanVar(value=settings.get("write_nfo",False));self.use_downloader=tk.BooleanVar(value=settings.get("use_downloader",False));self.preview_page=0;self.skip_page=0
        self.budget=tk.StringVar(value=str(settings.get("name_bytes",240)));self.task_name=tk.StringVar()
        self.preview_note=tk.StringVar(value="尚未扫描；请选择目录后生成只读预览。")
        self.filter=tk.StringVar(value="全部");self.status=tk.StringVar(value="填写执行端地址和连接密钥，先连接并读取存储区域。")
        self.source_map=tk.StringVar();self.dest_map=tk.StringVar()
        box=ttk.Frame(self.root,padding=14);box.pack(fill="both",expand=True)
        ttk.Label(box,text="保留原件，批量创建硬链接入口",font=("Microsoft YaHei UI",16,"bold")).pack(anchor="w")
        ttk.Label(box,text="浏览和预览只读；设置变化须重扫。普通创建不覆盖；已有 NFO 的标题更新须单独预览并确认。").pack(anchor="w",pady=6)
        form=ttk.Frame(box);form.pack(fill="x");self.form=form
        for row,label in enumerate(("执行端地址","连接密钥（不保存）","保存的任务 / 新任务名","存储区域","原件相对子目录","硬链接相对子目录","名称上限（UTF-8字节）")):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky="w",pady=4)
        ttk.Entry(form,textvariable=self.url).grid(row=0,column=1,sticky="ew",padx=10)
        self.connectbtn=ttk.Button(form,text="连接 / 读取区域",command=self.connect_agent);self.connectbtn.grid(row=0,column=2,sticky="ew")
        keybox=ttk.Frame(form);keybox.grid(row=1,column=1,sticky="ew",padx=10)
        self.token_entry=ttk.Entry(keybox,textvariable=self.token,show="*");self.token_entry.pack(side="left",fill="x",expand=True)
        self.keybtn=ttk.Button(keybox,text="选择密钥文件",command=self.choose_key_file);self.keybtn.pack(side="left",padx=4)
        self.manualbtn=ttk.Button(keybox,text="改为手动输入",command=self.clear_key_file);self.manualbtn.pack(side="left")
        self.key_note=tk.StringVar();ttk.Label(form,textvariable=self.key_note,wraplength=1100).grid(row=9,column=0,columnspan=3,sticky="w")
        self.update_key_note()
        self.historybtn=ttk.Button(form,text="任务历史",command=self.history);self.historybtn.grid(row=1,column=2,sticky="ew")
        self.tasks=ttk.Combobox(form,textvariable=self.task_name);self.tasks.grid(row=2,column=1,sticky="ew",padx=10);self.tasks.bind("<<ComboboxSelected>>",self.load_task)
        self.savebtn=ttk.Button(form,text="保存任务设置",command=self.save_task);self.savebtn.grid(row=2,column=2,sticky="ew")
        self.area=ttk.Combobox(form,textvariable=self.region_label,state="readonly");self.area.grid(row=3,column=1,sticky="ew",padx=10);self.area.bind("<<ComboboxSelected>>",self.area_changed)
        ttk.Label(form,text="子目录留空 = 该区域允许的根目录").grid(row=3,column=2,sticky="w")
        ttk.Entry(form,textvariable=self.source_rel,state="readonly").grid(row=4,column=1,sticky="ew",padx=10)
        self.sourcebtn=ttk.Button(form,text="浏览 NAS 原件目录",command=lambda:self.browse("source"));self.sourcebtn.grid(row=4,column=2,sticky="ew")
        ttk.Entry(form,textvariable=self.dest_rel).grid(row=5,column=1,sticky="ew",padx=10)
        self.destbtn=ttk.Button(form,text="浏览 NAS 硬链接目录",command=lambda:self.browse("destination"));self.destbtn.grid(row=5,column=2,sticky="ew")
        ttk.Spinbox(form,from_=80,to=240,textvariable=self.budget,width=8).grid(row=6,column=1,sticky="w",padx=10)
        ttk.Label(form,text="硬链接目录可填写尚不存在的相对子目录").grid(row=6,column=2,sticky="w")
        ttk.Label(form,text="下载保护：静置秒数").grid(row=7,column=0,sticky="w")
        options=ttk.Frame(form);options.grid(row=7,column=1,columnspan=2,sticky="w",padx=10,pady=4)
        ttk.Spinbox(options,from_=0,to=86400,textvariable=self.settle,width=8).pack(side="left")
        ttk.Label(options,text="默认300秒；0仍排除临时和空文件").pack(side="left",padx=8)
        self.nfo_option=ttk.Checkbutton(options,text="完整标题：新增 NFO（需 NAS 开启）",variable=self.write_nfo);self.nfo_option.pack(side="left",padx=8)
        ttk.Label(form,text="NFO 为新增的小型标题文本；Emby 需启用 NFO 读取并刷新对应影片。已有 NFO 保留，当前不自动改写。").grid(row=8,column=0,columnspan=3,sticky="w",pady=4)
        ttk.Checkbutton(form,text="qBittorrent 完成状态检查（需管理员配置区域连接和路径映射）",variable=self.use_downloader).grid(row=10,column=0,columnspan=3,sticky="w",pady=3)
        self.advanced_widgets=[w for row in (6,7,8,10) for w in form.grid_slaves(row=row)]
        for w in self.advanced_widgets:w.grid_remove()
        self.advancedbtn=ttk.Button(form,text="高级选项 ▸（命名、下载保护、可选元数据）",command=self.toggle_advanced);self.advancedbtn.grid(row=11,column=0,columnspan=3,sticky="w",pady=4)
        form.columnconfigure(1,weight=1)
        ttk.Label(box,textvariable=self.source_map,wraplength=1220).pack(anchor="w",pady=(6,0))
        ttk.Label(box,textvariable=self.dest_map,wraplength=1220).pack(anchor="w",pady=(2,6))
        bar=ttk.Frame(box);bar.pack(fill="x",pady=4)
        self.scanbtn=ttk.Button(bar,text="1. 扫描并生成预览（只读）",command=self.scan);self.scanbtn.pack(side="left")
        self.newbtn=ttk.Button(bar,text="检测待补充（只读）",command=lambda:self.scan(True));self.newbtn.pack(side="left",padx=4)
        self.exportbtn=ttk.Button(bar,text="导出预览 CSV",command=self.export,state="disabled");self.exportbtn.pack(side="left",padx=8)
        self.runbtn=ttk.Button(bar,text="2. 创建全部待创建项",command=self.execute,state="disabled");self.runbtn.pack(side="left")
        self.verifybtn=ttk.Button(bar,text="核验选中文件（只读）",command=self.verify_selected);self.verifybtn.pack(side="left",padx=8)
        ttk.Label(bar,text="仅筛选显示：").pack(side="left",padx=(18,4))
        ttk.Combobox(bar,textvariable=self.filter,values=("全部","待创建","已有硬链接","目标冲突","暂缓处理","已有元数据","元数据冲突","预览已过期"),state="readonly",width=14).pack(side="left")
        ttk.Label(box,textvariable=self.status,wraplength=1220).pack(anchor="w",pady=6)
        progressbar=ttk.Frame(box);progressbar.pack(fill="x",pady=4)
        self.progressbar=ttk.Progressbar(progressbar,maximum=100,variable=self.progress_value);self.progressbar.pack(side="left",fill="x",expand=True)
        self.stopbtn=ttk.Button(progressbar,text="当前文件完成后停止",command=self.stop_active,state="disabled");self.stopbtn.pack(side="left",padx=8)
        self.recoverbtn=ttk.Button(progressbar,text="恢复查看最近任务（只读）",command=self.recover_latest);self.recoverbtn.pack(side="left")
        ttk.Label(box,textvariable=self.progress_text,wraplength=1220).pack(anchor="w")
        self.attach_advanced(box)
        tabs=ttk.Notebook(box);tabs.pack(fill="both",expand=True)
        previewtab=ttk.Frame(tabs);skiptab=ttk.Frame(tabs);logtab=ttk.Frame(tabs)
        ttk.Label(previewtab,textvariable=self.preview_note).grid(row=2,column=0,sticky="w",pady=5)
        tabs.add(previewtab,text="文件预览");tabs.add(skiptab,text="跳过的项目");tabs.add(logtab,text="操作日志（本次打开期间）")
        self.tree=self.table(previewtab,[("status","状态",130),("reason","原因",240),("bytes","原名称字节",85),("old","原文件相对路径",430),("new","入口相对路径（含 NFO）",430),("kind","操作类型",110),("title","完整标题预览",400)])
        nav=ttk.Frame(previewtab);nav.grid(row=3,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Button(nav,text="上一页",command=lambda:self.move_preview_page(-1)).pack(side="left")
        ttk.Button(nav,text="下一页",command=lambda:self.move_preview_page(1)).pack(side="left",padx=6)
        self.page_note=tk.StringVar();ttk.Label(nav,textvariable=self.page_note).pack(side="left")
        self.skip_tree=self.table(skiptab,[("path","跳过的相对路径",650),("reason","原因",350)])
        skipnav=ttk.Frame(skiptab);skipnav.grid(row=3,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Button(skipnav,text="上一页",command=lambda:self.move_skip_page(-1)).pack(side="left")
        ttk.Button(skipnav,text="下一页",command=lambda:self.move_skip_page(1)).pack(side="left",padx=6)
        self.skip_note=tk.StringVar();ttk.Label(skipnav,textvariable=self.skip_note).pack(side="left")
        self.log=tk.Text(logtab,wrap="word",state="disabled");self.log.pack(fill="both",expand=True)
        for tag,color in (("existing","#267342"),("conflict","#a02020"),("deferred","#8a5800"),("expired","#777777")):self.tree.tag_configure(tag,foreground=color)
        self.tree.bind("<Double-1>",self.detail)
        ttk.Label(box,text="筛选只影响显示，不改变执行范围。原文件不删除、不改名、不改写；其他软件改写硬链接内容仍会影响原件。",wraplength=1220).pack(anchor="w",pady=8)
        for var in (self.url,self.token,self.region_id,self.source_rel,self.dest_rel,self.budget,self.settle,self.write_nfo,self.key_file,self.use_downloader):var.trace_add("write",self.edited)
        self.filter.trace_add("write",lambda *a:self.render())
        self.refresh_metadata_controls()
        self.root.protocol("WM_DELETE_WINDOW",self.close);self.root.after(100,self.pump);self.reload_tasks()
    @staticmethod
    def table(parent,columns):
        tree=ttk.Treeview(parent,columns=[c[0] for c in columns],show="headings")
        for key,title,width in columns:tree.heading(key,text=title);tree.column(key,width=width)
        sy=ttk.Scrollbar(parent,orient="vertical",command=tree.yview);sx=ttk.Scrollbar(parent,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        tree.grid(row=0,column=0,sticky="nsew");sy.grid(row=0,column=1,sticky="ns");sx.grid(row=1,column=0,sticky="ew")
        parent.columnconfigure(0,weight=1);parent.rowconfigure(0,weight=1);return tree
    def toggle_advanced(self):
        self.advanced_expanded=not self.advanced_expanded
        for widget in self.advanced_widgets:
            widget.grid() if self.advanced_expanded else widget.grid_remove()
        self.advancedbtn.configure(text=("高级选项 ▾" if self.advanced_expanded else "高级选项 ▸")+"（命名、下载保护、可选元数据）")
        self.refresh_metadata_controls()
    def refresh_metadata_controls(self):
        self.nfo_option.configure(state="normal" if self.metadata_allowed and not self.busy else "disabled")
        if not self.metadata_allowed and self.write_nfo.get():self.write_nfo.set(False)
        if self.metadata_allowed and self.advanced_expanded:self.metadatabtn.pack(side="left",padx=8)
        else:self.metadatabtn.pack_forget()
        self.metadatabtn.configure(state="normal" if self.metadata_allowed and not self.busy else "disabled")
    def freeze_form(self,freeze):
        if freeze:
            self.frozen_fields=[]
            def visit(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() in ("TEntry","TCombobox","TSpinbox","TCheckbutton"):
                        self.frozen_fields.append((child,str(child.cget("state"))));child.configure(state="disabled")
                    visit(child)
            visit(self.form)
        else:
            for widget,state in self.frozen_fields:
                if widget.winfo_exists():widget.configure(state=state)
            self.frozen_fields=[];self.update_key_note();self.refresh_metadata_controls()
    def settings_payload(self):
        return dict(url=self.url.get().strip(),name_bytes=int(self.budget.get()),settle_seconds=int(self.settle.get()),write_nfo=self.write_nfo.get(),key_file=self.key_file.get(),use_downloader=self.use_downloader.get())
    def save_client_settings(self,payload=None):
        payload=self.settings_payload() if payload is None else payload
        APP.mkdir(parents=True,exist_ok=True);temp=APP/"agent_settings.tmp"
        temp.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8");os.replace(temp,APP/"agent_settings.json")
    def update_key_note(self):
        filename=self.key_file.get()
        self.token_entry.configure(state="disabled" if filename else "normal")
        self.key_note.set("密钥来源：文件 "+filename+"（只记住路径，不复制密钥；连接时读取）" if filename else "密钥来源：手动输入；内容仅用于本次打开期间。")
    def choose_key_file(self):
        path=filedialog.askopenfilename(title="选择密钥文件（单行文本或 .env）",filetypes=[("密钥 / 环境文件","*.txt *.key *.env"),("所有文件","*")])
        if not path:return
        try:
            value=read_token_file(path);self.key_file.set(path);self.token.set(value);self.update_key_note();self.save_client_settings()
            self.logline("已选择密钥文件，仅保存文件路径；密钥内容不写入程序配置。")
        except Exception as e:messagebox.showerror("密钥文件",str(e))
    def clear_key_file(self):
        self.key_file.set("");self.token.set("");self.update_key_note()
        try:self.save_client_settings()
        except Exception as e:messagebox.showerror("保存密钥偏好",str(e))
    def close(self):
        if self.busy:
            if not self.monitoring:messagebox.showinfo("操作进行中","请等待当前只读操作结束。");return
            note="关闭后 NAS 只读核验继续，但报告仅保存在执行端内存；重开后可能需要重新核验。" if self.auditing else "关闭后 NAS 修改任务会继续执行，不会因此停止。提交编号已保存在本机，下次连接后可恢复查看。"
            if not messagebox.askyesno("关闭查看窗口",note+"\n\n确认关闭？",default="no"):return
        self.root.destroy()
    def logline(self,text):
        self.log.configure(state="normal");self.log.insert("end","["+datetime.now().strftime("%Y-%m-%d %H:%M:%S")+"] "+text.replace("\n","\n    ")+"\n");self.log.see("end");self.log.configure(state="disabled")
    def mark_expired(self,note):
        self.preview_note.set("预览已过期："+note+" 请重新只读扫描核对。")
        self.exportbtn.configure(state="disabled")
        for row in self.rows:
            if row["status"]=="待创建":row["status"]="预览已过期"
        self.render()
    def reload_tasks(self):
        try:self.saved=self.store.load();self.tasks["values"]=sorted(self.saved)
        except Exception as e:self.saved={};self.logline(str(e))
    def edited(self,*args):
        had=self.preview.plan is not None;self.preview.invalidate();self.runbtn.configure(state="disabled")
        if self.connected!=(self.url.get().strip(),self.token.get()):self.connected=None
        if had:
            self.preview_note.set("预览已失效：设置已改变，请重新扫描。")
            self.rows=[];self.skips=[];self.render();self.skip_tree.delete(*self.skip_tree.get_children());self.exportbtn.configure(state="disabled")
            self.status.set("目录、区域、连接或规则已改变，旧预览已失效，请重新扫描。")
        self.show_mapping()
    def show_mapping(self):
        region=self.regions.get(self.region_id.get())
        if not region:self.source_map.set("");self.dest_map.set("");return
        for var,key,display,rel,title in [(self.source_map,"source_root","source_display",self.source_rel.get(),"原件"),(self.dest_map,"destination_root","destination_display",self.dest_rel.get(),"硬链接")]:
            tail="/"+rel if rel else ""
            var.set(title+"：NAS位置说明 "+region[display].rstrip("/")+tail+"  → 容器 "+region[key].rstrip("/")+tail)
    def selection(self):
        rid=self.region_id.get()
        if rid not in self.regions:raise ValueError("请先连接并选择存储区域")
        budget=int(self.budget.get())
        if not 80<=budget<=240:raise ValueError("名称上限应为80～240字节")
        selected=dict(region_id=rid,source_relative=self.source_rel.get(),dest_relative=self.dest_rel.get(),name_bytes=budget,settle_seconds=int(self.settle.get()),write_nfo=self.write_nfo.get(),use_downloader=self.use_downloader.get())
        from media_features import validate_options
        validate_options(selected["settle_seconds"],selected["write_nfo"])
        for path in (selected["source_relative"],selected["dest_relative"]):
            if path.startswith(("/","\\")) or "\\" in path or (path and any(p in ("",".","..") for p in path.split("/"))):
                raise ValueError("只能使用区域内相对子目录，不允许绝对路径或 ..")
        return selected
    def api(self,require_connected=True):
        if self.key_file.get():
            value=read_token_file(self.key_file.get())
            if value!=self.token.get():self.token.set(value)
        url=self.url.get().strip();token=self.token.get();client=AgentClient(url,token)
        if require_connected and self.connected!=(url,token):raise ValueError("连接已改变，请先点击“连接 / 读取区域”")
        return client,(url,token)
    def current_key(self):
        try:return choice_key(self.url.get().strip(),self.token.get(),self.selection())
        except Exception:return None
    def start(self,work,operation="操作"):
        if self.busy:return
        self.busy=True
        self.freeze_form(True)
        self.refresh_stop_button()
        self.logline("──────── "+operation+" ────────")
        for button in self.buttons():button.configure(state="disabled")
        def worker():
            try:work()
            except Exception as e:self.q.put(("error",str(e)))
            finally:self.q.put(("done",None))
        threading.Thread(target=worker,daemon=True).start()
    def buttons(self):
        return (self.connectbtn,self.historybtn,self.savebtn,self.sourcebtn,self.destbtn,self.scanbtn,self.exportbtn,self.runbtn,self.verifybtn,self.newbtn,self.recoverbtn,self.keybtn,self.manualbtn,self.auditbtn,self.metadatabtn,self.advancedbtn)
    def connect_agent(self):
        try:client,key=self.api(False)
        except Exception as e:messagebox.showerror("连接设置",str(e));return
        self.preview.invalidate();self.runbtn.configure(state="disabled")
        def work():
            self.q.put(("status","正在读取区域配置……"));health=client.call("/health")
            if health.get("version")!=VERSION:raise RuntimeError("Windows 和 NAS 端请一起更新到 "+VERSION)
            if not health.get("delete_guard",{}).get("active"):raise RuntimeError("NAS 删除保护未启用，拒绝继续")
            regions=client.call("/regions")["regions"];self.q.put(("regions",(regions,key,health.get("features",{}))))
        try:self.save_client_settings()
        except Exception as e:messagebox.showerror("保存连接偏好",str(e));return
        self.start(work,"连接并读取区域")
    def apply_region(self,rid,task=None):
        region=self.regions[rid];self.region_id.set(rid);self.region_label.set(region["name"]+" ["+rid+"]")
        self.source_rel.set(task["source_relative"] if task else region.get("default_source_relative",""))
        self.dest_rel.set(task["dest_relative"] if task else region.get("default_dest_relative",""))
        if task:
            self.budget.set(str(task["name_bytes"]));self.settle.set(str(task.get("settle_seconds",300)));self.write_nfo.set(task.get("write_nfo",False))
        if not self.metadata_allowed and self.write_nfo.get():self.write_nfo.set(False)
        self.use_downloader.set(bool(region.get("require_downloader",False)) or bool(task.get("use_downloader",False) if task else self.use_downloader.get()))
        self.show_mapping()
    def area_changed(self,event=None):
        rid=self.region_labels.get(self.region_label.get())
        if rid:self.apply_region(rid)
    def save_task(self):
        try:
            self.api();selected=self.selection();task={"url":self.url.get().strip(),"key_file":self.key_file.get(),**selected}
            self.store.save(self.task_name.get(),task);self.reload_tasks();self.logline("任务设置已保存；没有保存密钥，也没有操作媒体。")
        except Exception as e:messagebox.showerror("保存任务",str(e))
    def load_task(self,event=None):
        task=self.saved.get(self.task_name.get())
        if not task:return
        clear=(self.url.get().strip()!=task["url"] or self.key_file.get()!=task.get("key_file",""))
        self.pending_task=task;self.url.set(task["url"]);self.key_file.set(task.get("key_file",""))
        if clear:self.token.set("")
        self.update_key_note()
        if self.connected==(task["url"],self.token.get()) and task["region_id"] in self.regions:
            self.apply_region(task["region_id"],task);self.pending_task=None
        else:self.status.set("任务已载入。填写连接密钥并连接，之后仍需重新预览。")
    def browse(self,side):
        try:
            client,key=self.api();region=self.regions[self.region_id.get()]
        except Exception as e:messagebox.showerror("选择目录",str(e));return
        rid=region["id"]
        def choose(rel):
            if self.connected!=key or self.region_id.get()!=rid:messagebox.showinfo("设置已改变","请重新打开目录浏览器");return
            if side=="source":
                old=self.source_rel.get();self.source_rel.set(rel)
                if self.dest_rel.get() in ("",old):self.dest_rel.set(rel)
            else:self.dest_rel.set(rel)
        DirectoryPicker(self.root,client,region,side,choose)
    def scan(self,new_only=False):
        try:client,connection=self.api();selection=self.selection();key=choice_key(*connection,selection)
        except Exception as e:messagebox.showerror("检查设置",str(e));return
        self.filter.set("待创建" if new_only else "全部")
        self.preview.invalidate();self.rows=[];self.skips=[];self.render();self.skip_tree.delete(*self.skip_tree.get_children())
        settings=self.settings_payload()
        def work():
            self.q.put(("status","正在只读扫描选定目录……"));plan=client.call("/preview",selection)
            if plan.get("version")!=VERSION:raise RuntimeError("执行端版本已变化，请重新连接")
            self.q.put(("plan",(plan,key)))
            self.save_client_settings(settings)
        self.start(work,"只读扫描")
    def move_skip_page(self,delta):
        self.skip_page=max(0,self.skip_page+delta);self.render_skips(False)
    def render_skips(self,reset=True):
        if reset:self.skip_page=0
        self.skip_page=min(self.skip_page,max(0,(len(self.skips)-1)//500))
        self.skip_tree.delete(*self.skip_tree.get_children())
        for skip in self.skips[self.skip_page*500:(self.skip_page+1)*500]:self.skip_tree.insert("","end",values=(skip["rel"],skip["reason"]))
        self.skip_note.set(f"第 {self.skip_page+1} 页；跳过共 {len(self.skips)} 项，单页最多500项")
    def move_preview_page(self,delta):
        self.preview_page=max(0,self.preview_page+delta);self.render(False)
    def render(self,reset=True):
        if reset:self.preview_page=0
        self.tree.delete(*self.tree.get_children());selected=self.filter.get()
        indices=[i for i,row in enumerate(self.rows) if selected=="全部" or row["status"]==selected]
        self.preview_page=min(self.preview_page,max(0,(len(indices)-1)//500))
        for i in indices[self.preview_page*500:(self.preview_page+1)*500]:
            row=self.rows[i];kind={"nfo":"新增 NFO","nfo_update":"更新 NFO"}.get(row.get("action"),"硬链接")
            self.tree.insert("","end",iid=str(i),values=(row["status"],row.get("reason",""),row["bytes"],row["rel"],row["newrel"],kind,row.get("title","")),tags=({"已有硬链接":"existing","目标冲突":"conflict","暂缓处理":"deferred","预览已过期":"expired"}.get(row["status"],""),))
        self.page_note.set(f"第 {self.preview_page+1} 页 / {max(1,(len(indices)+499)//500)} 页；筛选共 {len(indices)} 项，单页最多500项")
    def detail(self,event=None):
        self.verify_selected()
    def verify_selected(self):
        selected=self.tree.selection()
        if not selected:messagebox.showinfo("选择文件","请先在文件预览中选中一行，再点击核验。");return
        try:
            client,connection=self.api()
            if connection!=self.row_connection or not self.row_selection:raise ValueError("连接已变化，请重新扫描后核验")
            row=self.rows[int(selected[0])]
            if row.get("action")=="nfo":
                messagebox.showinfo("标题元数据预览","完整标题："+row["title"]+"\n目标："+row["dest"]+"\n"+row["reason"]+"\nNFO 是独立新增的标题文本，不是视频硬链接。");return
            request={"selection":self.row_selection,"source_relative":row["rel"],"dest_relative":row["newrel"]}
        except Exception as e:messagebox.showerror("无法核验",str(e));return
        def work():self.q.put(("verified",client.call("/verify",request)))
        self.start(work,"实时硬链接核验（只读）")
    def show_verification(self,result):
        win=tk.Toplevel(self.root);win.title("硬链接核验 · NAS 实时只读检查");win.geometry("960x640")
        box=ttk.Frame(win,padding=16);box.pack(fill="both",expand=True)
        shared=result["shared_data"];a=result["source"];b=result["destination"]
        conclusion="核验通过：原件与硬链接指向同一文件，共用一份数据。" if shared else ("未通过：原件或硬链接不存在。" if not a["exists"] or not b["exists"] else "未通过：未确认两条路径为同一文件的硬链接。")
        ttk.Label(box,text=conclusion,wraplength=920,font=("Microsoft YaHei UI",12,"bold")).pack(anchor="w")
        ttk.Label(box,text="核验时间："+display_time(result["checked_at"])+"；这是该时刻的结果，文件后续可能被其他程序改变。",wraplength=920).pack(anchor="w",pady=8)
        text=tk.Text(box,wrap="word",height=17);text.pack(fill="both",expand=True)
        lines=[]
        for title,entry,display in (("原件",a,result["source_display"]),("硬链接",b,result["destination_display"])):
            lines.extend([title+" NAS位置："+display,"容器路径："+entry["path"]])
            if entry["exists"]:lines.append(f"设备号：{entry['device']}    文件编号：{entry['inode']}    硬链接数：{entry['links']}    大小：{entry['size']:,} 字节")
            else:lines.append("文件不存在")
            lines.append("")
        lines.append("设备号与文件编号同时相同是身份依据。核验通过表示这两条路径共用数据，不代表整个文件夹的所有文件均已核验。")
        text.insert("1.0","\n".join(lines));text.configure(state="disabled")
        bar=ttk.Frame(box);bar.pack(fill="x",pady=10)
        def copy_path(path):win.clipboard_clear();win.clipboard_append(path)
        for title,entry in (("复制原件容器路径",a),("复制硬链接容器路径",b)):
            ttk.Button(bar,text=title,command=lambda path=entry["path"]:copy_path(path)).pack(side="left",padx=4)
        ttk.Button(bar,text="关闭",command=win.destroy).pack(side="right")
        self.logline(conclusion+" 核验时间："+display_time(result["checked_at"]))
    def export(self):
        path=filedialog.asksaveasfilename(defaultextension=".csv",initialfile="硬链接预览.csv",filetypes=[("CSV","*.csv")])
        if path:
            with open(path,"w",encoding="utf-8-sig",newline="") as f:
                w=csv.writer(f);w.writerow(["状态","原因","原名称字节","原文件","入口","操作类型","完整标题"])
                for r in self.rows:w.writerow([r["status"],r.get("reason",""),r["bytes"],r["source"],r["dest"],r.get("action","hardlink"),r.get("title","")])
    def execute(self):
        try:client,_=self.api();key=self.current_key()
        except Exception as e:messagebox.showerror("检查连接",str(e));return
        if not self.preview.executable(key):messagebox.showinfo("预览失效","请重新扫描，确认没有读取错误");return
        plan=self.preview.plan;count=sum(r["status"]=="待创建" and r.get("action")!="nfo" for r in self.rows);nfo_count=sum(r["status"]=="待创建" and r.get("action")=="nfo" for r in self.rows)
        if not messagebox.askyesno("这是修改操作：创建入口与标题文件",f"区域：{plan['region_name']}\n原件：{plan['source']}\n入口：{plan['dest']}\n\n将新增目录、{count} 个硬链接及 {nfo_count} 个标题 NFO 文件。NFO 使用原始视频完整主名作为标题，仅写入硬链接目录；已有文件保留。其他状态不会执行，不复制媒体、不删除、不覆盖。\n注意：当前筛选或选中行不改变范围，此次执行整个预览的全部待创建项。\n\n确认执行？",default="no"):return
        request=dict(plan_id=plan["plan_id"],confirmed="CREATE_HARDLINKS",selection=plan["selection"],task_name=self.task_name.get().strip())
        self.submit_confirmed(client,plan,request,"/execute")
    def submit_confirmed(self,client,plan,request,endpoint):
        if self.busy:return False
        try:
            receipt=self.recovery.remember(client.url,plan["plan_id"],task_name=request["task_name"])
        except Exception as e:messagebox.showerror("未提交创建","无法保存恢复记录，尚未提交 NAS。\n"+str(e));return
        self.preview.invalidate()
        self.mark_expired("已提交创建；此表为执行前预览，不代表当前文件状态。")
        self.active_receipt=receipt;self.monitor_client=client;self.monitoring=True
        self.progress_value.set(0);self.progress_text.set("正在提交；若响应丢失，可按提交编号恢复查看。")
        def work():
            try:
                job=client.call(endpoint,request)
                receipt["job_id"]=job["job_id"]
                try:self.recovery.remember(client.url,receipt["preview_id"],job["job_id"],receipt["task_name"])
                except Exception:self.q.put(("log","任务已提交，但本地任务编号更新失败。仍可通过提交编号恢复。"))
                self.poll_job(client,job,receipt)
            except Exception as e:self.q.put(("job_unknown",str(e)))
        self.start(work,"提交已确认的修改任务")
        return True
    def refresh_stop_button(self):
        enabled=bool(self.audit_id) if self.auditing else (self.monitoring and self.active_receipt and self.active_receipt.get("job_id"))
        self.stopbtn.configure(state="normal" if enabled else "disabled")
    def poll_job(self,client,job,receipt):
        self.q.put(("log","任务编号："+job["job_id"]))
        while True:
            self.q.put(("job_progress",dict(job)))
            if job.get("done"):self.q.put(("job_finished",dict(job)));return
            time.sleep(1)
            job=client.call("/jobs/"+job["job_id"])
    def stop_active(self):
        if self.auditing and not self.audit_id:return
        if self.audit_id:
            aid=self.audit_id;client=self.audit_client
            self.stopbtn.configure(state="disabled")
            def stop_audit():
                try:self.q.put(("log",client.call("/audit/stop",{"audit_id":aid})["message"]))
                except Exception as e:self.q.put(("log","停止核验请求未确认："+str(e)))
            threading.Thread(target=stop_audit,daemon=True).start();return
        if not self.monitoring or not self.active_receipt or not self.active_receipt.get("job_id"):return
        if not messagebox.askyesno("安全停止","将请求 NAS 在当前文件处理完成后停止。已创建的硬链接和 NFO 会保留，不回滚、不删除。\n\n确认请求停止？",default="no"):return
        client=self.monitor_client;jid=self.active_receipt["job_id"];self.stopbtn.configure(state="disabled")
        self.logline("请求安全停止：当前文件完成后停止，保留已完成内容。")
        def work():
            try:self.q.put(("stop_ack",(jid,client.call("/stop",{"job_id":jid,"confirmed":"STOP_AFTER_CURRENT"}))))
            except Exception as e:self.q.put(("stop_error",str(e)))
        threading.Thread(target=work,daemon=True).start()
    def recover_latest(self,receipt=None):
        if self.busy:return
        try:
            client,key=self.api()
            receipt=receipt or self.recovery.latest(client.url)
            if not receipt:
                self.history();return
        except Exception as e:messagebox.showerror("恢复查看",str(e));return
        self.preview.invalidate();self.runbtn.configure(state="disabled")
        if self.rows:self.mark_expired("正在恢复原任务状态。")
        self.active_receipt=dict(receipt);self.monitor_client=client;self.monitoring=True
        self.progress_text.set("正在查询原任务；不会重新提交创建。")
        def work():
            try:
                result=client.call("/recover",{"job_id":receipt.get("job_id",""),"preview_id":receipt.get("preview_id","")})
                if not result["found"]:self.q.put(("job_unknown",result["message"]));return
                job=result["job"];self.active_receipt["job_id"]=job["job_id"]
                self.poll_job(client,job,self.active_receipt)
            except Exception as e:self.q.put(("job_unknown",str(e)))
        self.start(work,"恢复查看原任务（只读，不重新创建）")
    def history(self):
        try:client,key=self.api()
        except Exception as e:messagebox.showerror("连接设置",str(e));return
        def work():self.q.put(("history",(client.call("/history"),key)))
        self.start(work,"读取任务历史（只读）")
    def show_history(self,result):
        win=tk.Toplevel(self.root);win.title("NAS 任务历史（本机时间）");win.geometry("1100x520")
        tree=self.table(win,[("time","开始时间",160),("name","任务 / 区域",220),("status","状态",140),("count","已处理 / 计划",110),("error","错误",420)])
        jobs=result["jobs"]
        for i,job in enumerate(jobs):tree.insert("","end",iid=str(i),values=(display_time(job.get("started_at")),job.get("task_name") or job.get("region_name",""),classify(job)[1],str(job.get("processed",job.get("created",0)))+" / "+str(job.get("total",0)),job.get("error","")))
        def detail(event=None):
            selected=tree.selection()
            if selected:
                j=jobs[int(selected[0])];messagebox.showinfo("任务详情","任务："+j.get("task_name","")+"\n区域："+j.get("region_name","")+"\n开始："+display_time(j.get("started_at"))+"\n结束："+display_time(j.get("finished_at"))+"\n原件："+j.get("source","")+"\n入口："+j.get("dest","")+"\n硬链接："+str(j.get("created",0))+"；新增 NFO："+str(j.get("metadata_created",0))+"\n错误："+j.get("error",""),parent=win)
        tree.bind("<Double-1>",detail)
        expected=(self.url.get().strip(),self.token.get())
        def resume():
            selected=tree.selection()
            if not selected:return
            if expected!=(self.url.get().strip(),self.token.get()):messagebox.showinfo("连接已变化","请重新读取任务历史。",parent=win);return
            j=jobs[int(selected[0])]
            receipt=dict(url=expected[0],preview_id=j.get("preview_id",""),job_id=j["job_id"],task_name=j.get("task_name",""))
            win.destroy();self.recover_latest(receipt)
        bar=ttk.Frame(win);bar.grid(row=2,column=0,columnspan=2,sticky="ew",padx=10,pady=8)
        ttk.Button(bar,text="恢复查看选中任务（只读）",command=resume).pack(side="left")
        ttk.Label(bar,text="恢复只查询原任务，不自动重试；未确认结果请只读扫描复核。").pack(side="left",padx=12)
        for warning in result.get("warnings",[]):self.logline(warning)
    def pump(self):
        try:
            while True:
                kind,data=self.q.get_nowait()
                if kind=="status":self.status.set(data)
                elif kind=="log":self.logline(data)
                elif kind=="error":
                    self.status.set("本次操作未完成，请查看日志；若已提交创建，NAS 可能仍在执行，应查看历史或只读扫描核对。")
                    self.logline("本次操作未完成："+data);messagebox.showerror("操作未完成",data)
                elif kind=="ui_callback":data()
                elif kind=="metadata_plan":
                    client,plan,bound=data;self.show_metadata_plan(client,plan,bound)
                elif kind=="audit_progress":
                    self.progress_value.set(100*data["processed"]/data["total"] if data["total"] else 0)
                    self.progress_text.set(data["phase"]+f"：{data['processed']} / {data['total']}；"+str(data["counts"]))
                    self.refresh_stop_button()
                elif kind=="audit_finished":
                    client,result=data;self.monitoring=False;self.auditing=False;self.audit_id=None;self.refresh_stop_button()
                    self.status.set(result["phase"]+"；"+str(result["counts"]))
                    self.logline(self.status.get());self.show_audit_report(client,result)
                elif kind=="audit_error":
                    self.monitoring=False;self.auditing=False;self.audit_id=None;self.refresh_stop_button()
                    self.status.set("批量只读核验未完成；报告不能视为全部通过。")
                    self.logline(str(data));messagebox.showerror("核验未完成",str(data))
                elif kind=="job_progress":
                    total=data.get("total",0);processed=data.get("processed",data.get("created",0))
                    self.progress_value.set(100*processed/total if total else 0)
                    self.progress_text.set(summary(data)+" 当前："+(data.get("current_file") or "准备或收尾中"))
                    self.status.set(classify(data)[1]+"；任务编号："+data["job_id"])
                    self.refresh_stop_button()
                    if data.get("stop_requested"):self.stopbtn.configure(state="disabled")
                elif kind=="job_finished":
                    self.monitoring=False;self.refresh_stop_button()
                    result=summary(data);self.status.set(result);self.progress_text.set(result)
                    self.mark_expired("原任务已结束。")
                    self.logline(result+" 任务编号："+data["job_id"]+(("\n错误："+data["error"]) if data.get("error") else ""))
                    show=messagebox.showinfo if classify(data)[0]=="success" else messagebox.showwarning
                    show("任务结果："+classify(data)[1],result+"\n"+data.get("error","")+"\n请重新只读扫描核对，继续整理需重新预览并确认。")
                elif kind=="job_unknown":
                    self.monitoring=False;self.refresh_stop_button()
                    result=summary({"status":"unknown","done":True})
                    self.status.set(result);self.progress_text.set(result);self.logline(result+"\n连接或查询信息："+data)
                    messagebox.showwarning("任务状态未知",result+"\n"+data)
                elif kind=="stop_ack":
                    jid,result=data;self.logline(result["message"]+" 任务编号："+jid)
                    if self.monitoring and self.active_receipt and self.active_receipt.get("job_id")==jid:
                        self.status.set(result["message"]);self.stopbtn.configure(state="disabled")
                elif kind=="stop_error":
                    self.logline("停止请求未确认："+data)
                    messagebox.showwarning("停止请求未确认","不能认为 NAS 已停止。请恢复查看或再次请求。\n"+data)
                    self.refresh_stop_button()
                elif kind=="regions":
                    regions,key,features=data
                    self.metadata_allowed=bool(features.get("metadata_writes",False))
                    if key!=(self.url.get().strip(),self.token.get()):self.logline("连接参数已改变，丢弃旧区域结果。");continue
                    self.connected=key;self.regions={r["id"]:r for r in regions};self.region_labels={r["name"]+" ["+r["id"]+"]":r["id"] for r in regions}
                    self.area["values"]=list(self.region_labels)
                    task=self.pending_task
                    if task and task["region_id"] in self.regions:self.apply_region(task["region_id"],task);self.pending_task=None
                    else:
                        self.apply_region(regions[0]["id"])
                        if task:messagebox.showinfo("区域已变更","保存任务的区域不存在，请重新选择。");self.pending_task=None
                    self.status.set("已连接，删除保护已启用。可只读预览，或点击“恢复查看最近任务”。")
                elif kind=="plan":
                    plan,key=data
                    if not self.preview.accept(plan,key,self.current_key()):self.logline("扫描期间设置已变化，本次结果已丢弃。");continue
                    self.preview_note.set("执行前只读预览；暂缓文件请静置后重扫。下载保护是启发式检查，不能保证下载已完成；已有 NFO 不覆盖。")
                    self.row_selection=dict(plan["selection"]);self.row_connection=(self.url.get().strip(),self.token.get())
                    self.rows=plan["rows"];self.skips=plan.get("skipped_entries",[]);self.render();self.skip_tree.delete(*self.skip_tree.get_children())
                    self.render_skips()
                    for warning in plan.get("warnings",[]):self.logline("提示："+warning)
                    for error in plan["errors"]:self.logline(error)
                    counts={status:sum(r["status"]==status for r in self.rows) for status in ("待创建","已有硬链接","目标冲突","暂缓处理","已有元数据","元数据冲突")}
                    self.status.set(f"预览：{len(self.rows)} 文件，待创建 {counts['待创建']}，已有 {counts['已有硬链接']}，冲突 {counts['目标冲突']}，暂缓 {counts['暂缓处理']}，已有元数据 {counts['已有元数据']}，元数据冲突 {counts['元数据冲突']}，读取错误 {len(plan['errors'])}，跳过入口 {plan['skipped']}。未修改媒体。")
                elif kind=="verified":self.show_verification(data)
                elif kind=="history":
                    result,key=data
                    if key==(self.url.get().strip(),self.token.get()):self.show_history(result)
                    else:self.logline("连接已变化，丢弃旧任务历史。")
                elif kind=="done":
                    self.busy=False
                    self.freeze_form(False)
                    self.refresh_stop_button()
                    for b in self.buttons():b.configure(state="normal")
                    if not self.rows or self.preview.plan is None:self.exportbtn.configure(state="disabled")
                    if not self.preview.executable(self.current_key()):self.runbtn.configure(state="disabled")
                    self.refresh_metadata_controls()
        except queue.Empty:pass
        self.root.after(100,self.pump)
if __name__=="__main__":
    app=Studio()
    if "--self-test" in sys.argv:
        app.root.update();Path(sys.argv[sys.argv.index("--self-test")+1]).write_text(json.dumps(dict(gui="ok",version=CLIENT_VERSION,features=["hardened-directory-io","metadata-writes-off-by-default","frozen-execution-settings","batch-audit","paged-preview","qbit-reader","nfo-title-maintenance","safe-stop","recover-jobs","result-classes","token-file","incremental-preview","download-protection","full-title-nfo","regions","directory-picker","saved-tasks","preview-invalidation","history"])),encoding="utf-8")
        app.root.after(100,app.root.destroy)
    app.root.mainloop()
