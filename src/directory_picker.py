"""Asynchronous, region-scoped NAS directory picker."""
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox
class DirectoryPicker:
    def __init__(self,parent,client,region,side,on_choose):
        self.win=tk.Toplevel(parent);self.win.title("选择原件目录" if side=="source" else "选择硬链接目录")
        self.win.geometry("800x540");self.win.transient(parent);self.win.grab_set()
        self.client=client;self.region=region;self.side=side;self.on_choose=on_choose;self.q=queue.Queue()
        self.closed=False;self.paths={};self.loaded=set();self.loading=set()
        ttk.Label(self.win,text="仅浏览管理员允许的范围；选择目录不会创建或修改文件。展开文件夹查看子目录。回收站和软链接不显示。",wraplength=760).pack(anchor="w",padx=12,pady=10)
        frame=ttk.Frame(self.win);frame.pack(fill="both",expand=True,padx=12)
        self.tree=ttk.Treeview(frame,show="tree");scroll=ttk.Scrollbar(frame,orient="vertical",command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set);self.tree.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
        self.note=tk.StringVar(value="正在读取区域根目录……");ttk.Label(self.win,textvariable=self.note,wraplength=760).pack(anchor="w",padx=12,pady=8)
        bar=ttk.Frame(self.win);bar.pack(fill="x",padx=12,pady=12)
        self.choosebtn=ttk.Button(bar,text="选择此目录",command=self.choose,state="disabled");self.choosebtn.pack(side="right")
        ttk.Button(bar,text="取消",command=self.close).pack(side="right",padx=8)
        self.root_node=self.tree.insert("","end",text=("原件目录" if side=="source" else "硬链接目录")+" /",open=True);self.paths[self.root_node]=""
        self.tree.selection_set(self.root_node);self.tree.focus(self.root_node)
        self.tree.bind("<<TreeviewOpen>>",self.open_node);self.tree.bind("<<TreeviewSelect>>",self.show_path)
        self.win.protocol("WM_DELETE_WINDOW",self.close)
        self.request(self.root_node);self.win.after(100,self.pump)
    def close(self):
        self.closed=True;self.win.destroy()
    def request(self,node):
        if node in self.loaded or node in self.loading:return
        self.loading.add(node);rel=self.paths[node]
        def work():
            try:self.q.put((node,self.client.call("/browse",{"region_id":self.region["id"],"side":self.side,"relative":rel}),None))
            except Exception as e:self.q.put((node,None,str(e)))
        threading.Thread(target=work,daemon=True).start()
    def open_node(self,event=None):
        node=self.tree.focus()
        if node in self.paths:self.request(node)
    def show_path(self,event=None):
        node=self.tree.focus()
        if node not in self.paths:return
        root=self.region["source_display" if self.side=="source" else "destination_display"]
        self.choosebtn.configure(state="normal" if node in self.loaded else "disabled")
        rel=self.paths[node];self.note.set(root.rstrip("/")+("/"+rel if rel else ""))
    def choose(self):
        node=self.tree.focus()
        if node not in self.paths or node not in self.loaded:return
        # Root may not exist yet for output; server still validates before preview.
        self.on_choose(self.paths[node]);self.close()
    def pump(self):
        if self.closed:return
        try:
            while True:
                node,result,error=self.q.get_nowait();self.loading.discard(node)
                if error:
                    self.choosebtn.configure(state="disabled");self.note.set("目录读取失败："+error);messagebox.showerror("目录读取失败",error,parent=self.win);continue
                for child in self.tree.get_children(node):self.tree.delete(child)
                for entry in result["directories"]:
                    child=self.tree.insert(node,"end",text=entry["name"]);self.paths[child]=entry["relative"]
                    self.tree.insert(child,"end",text="展开以读取……")
                self.loaded.add(node)
                if self.tree.focus()==node:
                    self.choosebtn.configure(state="normal")
                    self.note.set(result["display_path"]+("" if result["exists"] else "（尚未创建，将在确认执行时创建）"))
        except queue.Empty:pass
        self.win.after(100,self.pump)
