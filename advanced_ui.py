"""Advanced Windows workflows: paged read-only reports and explicit NFO title updates."""
import csv,threading,time,tkinter as tk
from tkinter import ttk,messagebox,filedialog
PAGE=500
class AdvancedUI:
    def attach_advanced(self,parent):
        self.audit_id=None;self.audit_client=None;self.auditing=False
        bar=ttk.Frame(parent);bar.pack(fill="x",pady=3)
        self.auditbtn=ttk.Button(bar,text="批量核验目录（只读）",command=self.start_audit);self.auditbtn.pack(side="left")
        self.metadatabtn=ttk.Button(bar,text="已有 NFO 标题差异预览",command=self.start_metadata_preview);self.metadatabtn.pack(side="left",padx=8)
        ttk.Label(bar,text="核验只读；默认由外部刮削器维护资料，NFO 功能需在高级选项及 NAS 端开启。").pack(side="left")
    def start_audit(self):
        try:client,key=self.api();selection=self.selection()
        except Exception as e:messagebox.showerror("批量核验",str(e));return
        self.monitoring=True;self.auditing=True;self.audit_client=client;self.audit_id=None
        self.progress_value.set(0);self.progress_text.set("正在启动只读核验；不会创建媒体或 NAS 报告文件。")
        def work():
            try:
                result=client.call("/audit/start",selection);self.audit_id=result["audit_id"]
                while True:
                    page=client.call("/audit/page",{"audit_id":self.audit_id,"offset":0,"limit":1})
                    self.q.put(("audit_progress",page))
                    if page["done"]:self.q.put(("audit_finished",(client,page)));return
                    time.sleep(1)
            except Exception as error:self.q.put(("audit_error",str(error)))
        self.start(work,"批量只读核验")
    def show_audit_report(self,client,result):
        win=tk.Toplevel(self.root);win.title("批量核验报告 · 只读");win.geometry("1180x620")
        header=tk.StringVar(value="正常/缺失/冲突/无法核验："+str(result.get("counts",{}))+"；跳过 "+str(result.get("skipped_count",0))+" 项")
        ttk.Label(win,textvariable=header,wraplength=1140).pack(anchor="w",padx=10,pady=8)
        ttk.Label(win,text="报告为核验时刻的文件身份结果，不包含目的目录额外孤立文件；新核验或服务重启会使旧报告失效。",wraplength=1140).pack(anchor="w",padx=10)
        frame=ttk.Frame(win);frame.pack(fill="both",expand=True,padx=10)
        tree=self.table(frame,[("status","结果",100),("path","原件相对路径",400),("reason","原因",400),("links","链接数",80)])
        bar=ttk.Frame(win);bar.pack(fill="x",padx=10,pady=8);note=tk.StringVar();offset=[0];current=[[]];closed=[False]
        def render(page):
            if closed[0] or page["offset"]!=offset[0]:return
            tree.delete(*tree.get_children());current[0]=page["rows"]
            for i,row in enumerate(page["rows"]):tree.insert("","end",iid=str(i),values=(row["status"],row["relative"],row["reason"],row.get("links","")))
            note.set(f"第 {offset[0]//PAGE+1} 页，共 {page['row_count']} 条；单页最多 {PAGE} 条")
            header.set(str(page.get("counts",{}))+("；已停止，报告不完整" if page.get("cancelled") else "")+("；错误："+str(page["errors"]) if page.get("errors") else ""))
        def load(delta=0):
            desired=max(0,offset[0]+delta*PAGE)
            if desired>=result["row_count"] and desired:return
            offset[0]=desired;note.set("正在读取报告页……")
            def work():
                try:
                    page=client.call("/audit/page",{"audit_id":result["audit_id"],"offset":desired,"limit":PAGE})
                    self.q.put(("ui_callback",lambda:render(page)))
                except Exception as e:
                    error=str(e);self.q.put(("ui_callback",lambda:self.report_error(win,closed,error)))
            threading.Thread(target=work,daemon=True).start()
        def export():
            path=filedialog.asksaveasfilename(parent=win,defaultextension=".csv",initialfile="硬链接批量核验报告.csv",filetypes=[("CSV","*.csv")])
            if not path:return
            def work():
                with open(path,"w",encoding="utf-8-sig",newline="") as stream:
                    fields=["status","relative","source","destination","reason","device","inode","links","size","checked_at"]
                    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore");writer.writeheader()
                    for start in range(0,result["row_count"],PAGE):
                        page=client.call("/audit/page",{"audit_id":result["audit_id"],"offset":start,"limit":PAGE})
                        writer.writerows(page["rows"])
                self.q.put(("log","核验报告 CSV 已导出："+path))
            self.start(work,"导出只读报告到 Windows CSV")
        def details(event=None):
            selected=tree.selection()
            if selected:messagebox.showinfo("核验详情",str(current[0][int(selected[0])]),parent=win)
        tree.bind("<Double-1>",details)
        ttk.Button(bar,text="上一页",command=lambda:load(-1)).pack(side="left")
        ttk.Button(bar,text="下一页",command=lambda:load(1)).pack(side="left",padx=6)
        ttk.Button(bar,text="导出全部报告 CSV",command=export).pack(side="left")
        ttk.Label(bar,textvariable=note).pack(side="left",padx=10)
        def close():closed[0]=True;win.destroy()
        win.protocol("WM_DELETE_WINDOW",close);load()
    def report_error(self,win,closed,error):
        if not closed[0]:messagebox.showerror("报告读取失败",error,parent=win)
    def start_metadata_preview(self):
        try:client,key=self.api();selection=self.selection();bound=self.current_key()
        except Exception as e:messagebox.showerror("NFO 标题预览",str(e));return
        def work():self.q.put(("metadata_plan",(client,client.call("/metadata/preview",selection),bound)))
        self.start(work,"读取已有 NFO 标题差异（只读）")
    def show_metadata_plan(self,client,plan,bound):
        if bound!=self.current_key():self.logline("设置已变化，NFO 预览已丢弃。");return
        win=tk.Toplevel(self.root);win.title("已有 NFO 标题差异 · 确认前只读");win.geometry("1200x650")
        eligible=[r for r in plan["rows"] if r["status"]=="待创建"]
        ttk.Label(win,text=f"可更新 {len(eligible)}，总计 {len(plan['rows'])}；只修改 title/originaltitle，原内容先备份到 NAS state/nfo-backups。",wraplength=1150).pack(anchor="w",padx=10,pady=8)
        ttk.Label(win,text="与其他名称共用数据的 NFO、软链接、无对应视频硬链接或解析失败的 NFO 不改写。",wraplength=1150).pack(anchor="w",padx=10)
        frame=ttk.Frame(win);frame.pack(fill="both",expand=True,padx=10)
        tree=self.table(frame,[("status","状态",100),("old","当前标题",310),("new","建议完整标题",390),("reason","说明",400)])
        page=[0];bar=ttk.Frame(win);bar.pack(fill="x",padx=10,pady=8);note=tk.StringVar()
        def render():
            tree.delete(*tree.get_children())
            for row in plan["rows"][page[0]*PAGE:(page[0]+1)*PAGE]:
                tree.insert("","end",iid=row["row_id"],values=(row["status"],row.get("old_title",""),row["new_title"],row["reason"]))
            note.set(f"第 {page[0]+1} 页，单页最多 {PAGE} 条")
        def move(delta):
            target=max(0,page[0]+delta)
            if target*PAGE<len(plan["rows"]):page[0]=target;render()
        def details(event=None):
            ids=tree.selection()
            if ids:
                row=next(r for r in plan["rows"] if r["row_id"]==ids[0])
                messagebox.showinfo("标题差异",f"文件：{row['dest']}\n当前标题：{row.get('old_title','')}\n当前原标题：{row.get('old_original_title','')}\n建议：{row['new_title']}\n{row['reason']}",parent=win)
        def execute(all_rows=False):
            if self.busy:return
            try:
                live,_=self.api()
                if self.current_key()!=bound:raise ValueError("连接、目录或规则已变化，请重新预览")
            except Exception as e:messagebox.showerror("NFO 预览失效",str(e),parent=win);return
            ids="ALL_ELIGIBLE" if all_rows else list(tree.selection())
            chosen=eligible if all_rows else [r for r in eligible if r["row_id"] in ids]
            if not chosen or (not all_rows and len(chosen)!=len(ids)):messagebox.showinfo("选择更新项","请选择可更新行；不包含已一致或拒绝更新的项目。",parent=win);return
            if plan["errors"]:messagebox.showerror("扫描存在错误","请解决扫描错误后重新预览，不允许更新。",parent=win);return
            backup_bytes=sum(r["nfo_snapshot"][2] for r in chosen)
            if not messagebox.askyesno("这是修改操作：更新已有 NFO",f"将更新 {len(chosen)} 个入口 NFO 的标题，先保存原文件备份。\n预计备份大小：{backup_bytes/1048576:.2f} MiB。\n仅操作独立 NFO，不改写硬链接 NFO，不删除或改名原件。\n\n确认按预览差异更新？",parent=win,default="no"):return
            request=dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="UPDATE_NFO_TITLES",row_ids=ids,task_name=("[NFO标题] "+self.task_name.get())[:80])
            if self.submit_confirmed(live,plan,request,"/metadata/execute"):win.destroy()
        tree.bind("<Double-1>",details)
        ttk.Button(bar,text="上一页",command=lambda:move(-1)).pack(side="left")
        ttk.Button(bar,text="下一页",command=lambda:move(1)).pack(side="left",padx=6)
        ttk.Label(bar,textvariable=note).pack(side="left",padx=8)
        ttk.Button(bar,text="确认更新选中项",command=execute).pack(side="right")
        ttk.Button(bar,text="确认更新全部可更新项",command=lambda:execute(True)).pack(side="right",padx=8)
        render()
