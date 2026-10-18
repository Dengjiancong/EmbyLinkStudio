"""NAS hardlink preview and creation. Never modifies source contents or names."""
import csv
import hashlib
import json
import os
import posixpath as pp
import queue
import stat
import threading
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import paramiko

VERSION = '0.3.0'

def excluded_path(path):
    names = ('.trash', '#recycle', '@recycle', '$recycle.bin', '.recycle', '.recyclebin')
    return any(part.casefold() in names or part.casefold().startswith('.trash-')
               for part in path.split('/') if part)

APP = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'EmbyLinkStudio'

class LinkSFTPClient(paramiko.SFTPClient):
    """Capture advertised extensions; Paramiko 5 otherwise discards them."""
    def _send_version(self):
        from paramiko import Message
        from paramiko.sftp import CMD_INIT, CMD_VERSION, SFTPError
        message=Message();message.add_int(3);self._send_packet(CMD_INIT,message)
        kind,data=self._read_packet()
        if kind!=CMD_VERSION:raise SFTPError('服务未返回 SFTP 版本信息')
        reply=Message(data);version=reply.get_int();self.extensions={}
        while reply.get_remainder():
            name=reply.get_text();value=reply.get_text();self.extensions[name]=value
        return version

def require_hardlink(sf):
    if getattr(sf,'extensions',{}).get('hardlink@openssh.com')!='1':
        raise RuntimeError('当前 SFTP 服务未声明支持 hardlink@openssh.com v1。可以扫描，但不能通过此连接创建硬链接。没有转为复制，也未尝试执行远程命令。')

def create_hardlink(sf,source,dest):
    from paramiko.sftp import CMD_EXTENDED, CMD_STATUS
    require_hardlink(sf)
    kind,_=checked(f'SFTP 创建硬链接 {dest}',sf._request,CMD_EXTENDED,'hardlink@openssh.com',source,dest)
    if kind!=CMD_STATUS:raise RuntimeError('服务器未返回标准硬链接状态，请检查目标，暂勿重试：'+dest)

def checked(action, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except FileNotFoundError:
        raise
    except PermissionError as error:
        raise PermissionError(f'{action}：权限不足。请检查对应账户和目录权限，不要直接放宽整个媒体库权限。\n{error}') from error
    except OSError as error:
        raise OSError(f'{action}失败：{error}') from error

def short_component(name, budget=100):
    if len(name.encode('utf-8')) <= budget:
        return name
    suffix = '_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]
    prefix = name.encode('utf-8')[:budget-len(suffix)].decode('utf-8', 'ignore')
    return prefix + suffix

def target_relative(rel):
    parts = rel.split('/')
    base, ext = pp.splitext(parts[-1])
    parts[-1] = short_component(base, 80) + short_component(ext, 32)
    parts[:-1] = [short_component(x, 80) for x in parts[:-1]]
    return '/'.join(parts)

def overlap(a,b):
    return a == b or a.startswith(b.rstrip('/')+'/') or b.startswith(a.rstrip('/')+'/')

class Studio:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f'Emby 硬链接整理 v{VERSION} · 预览优先')
        self.root.geometry('1180x780')
        self.q = queue.Queue()
        self.busy = False
        self.rows = []
        self.plan = None
        self.values = {}
        self.config = {}
        try:
            self.config = json.loads((APP/'settings.json').read_text('utf-8'))
        except (OSError, ValueError): pass
        box=ttk.Frame(self.root,padding=16);box.pack(fill='both',expand=True)
        ttk.Label(box,text='保留原名，为 Emby 建立短名称入口',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(box,text='只创建文件夹和硬链接；不重命名、不删除、不覆盖，不在失败时改用复制。').pack(anchor='w',pady=(4,12))
        form=ttk.Frame(box);form.pack(fill='x')
        fields=[('host','NAS 局域网 IP',''),('port','SFTP 端口','2022'),('user','SSH 用户名',''),('password','SSH 密码（不保存）',''),('source','NAS 原始目录','/nas/media/originals/sample_author'),('dest','NAS 短名入口目录','/nas/media/hardlinks/sample_author')]
        for i,(key,label,default) in enumerate(fields):
            ttk.Label(form,text=label).grid(row=i,column=0,sticky='w',pady=4)
            var=tk.StringVar(value=self.config.get(key,default) if key!='password' else '')
            self.values[key]=var
            ttk.Entry(form,textvariable=var,show='*' if key=='password' else '').grid(row=i,column=1,sticky='ew',padx=(16,0),pady=4)
        form.columnconfigure(1,weight=1)
        ttk.Label(box,text='填写 NAS 的实际路径（WinSCP 中可查看），不要填 Y:\\ 或容器路径 /data。源目录与入口目录不能互相包含。',wraplength=1120).pack(anchor='w',pady=8)
        bar=ttk.Frame(box);bar.pack(fill='x',pady=8)
        self.scanbutton=ttk.Button(bar,text='1. 扫描并生成预览（只读）',command=self.scan);self.scanbutton.pack(side='left')
        self.exportbutton=ttk.Button(bar,text='导出预览 CSV',command=self.export,state='disabled');self.exportbutton.pack(side='left',padx=8)
        self.runbutton=ttk.Button(bar,text='2. 确认后创建硬链接',command=self.execute,state='disabled');self.runbutton.pack(side='left')
        ttk.Label(box,text='命名规则：目录名和文件主名上限 80 UTF-8 字节；超出时保留开头并添加稳定编号。相同原主名的 MP4/JPG 使用相同短主名。原始视频和图片主名不同的，不自动猜测配对。',wraplength=1120).pack(anchor='w',pady=4)
        self.status=tk.StringVar(value='尚未连接 NAS。先使用测试目录。')
        ttk.Label(box,textvariable=self.status).pack(anchor='w',pady=6)
        area=ttk.Frame(box);area.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(area,columns=('status','bytes','old','new'),show='headings',selectmode='browse')
        for k,title,w in [('status','状态',120),('bytes','原名称字节',90),('old','原文件相对路径',430),('new','短名入口相对路径',430)]:
            self.tree.heading(k,text=title);self.tree.column(k,width=w,minwidth=60)
        sy=ttk.Scrollbar(area,orient='vertical',command=self.tree.yview);sx=ttk.Scrollbar(area,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
        area.columnconfigure(0,weight=1);area.rowconfigure(0,weight=1)
        self.tree.bind('<Double-1>',self.detail)
        self.log=tk.Text(box,height=6,wrap='word',state='disabled');self.log.pack(fill='x',pady=(8,0))
        ttk.Label(box,text='双击预览行查看完整路径。密码仅用于本次连接；首次连接会核对 SSH 主机指纹。').pack(anchor='w')
        self.root.after(100,self.pump)
        self.root.protocol('WM_DELETE_WINDOW',self.close)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中','请等待当前操作结束后关闭，避免中断创建。');return
        self.root.destroy()

    def detail(self,event=None):
        sel=self.tree.selection()
        if sel:
            r=self.rows[int(sel[0])]
            messagebox.showinfo('路径详情',f"状态：{r['status']}\n\n原路径：\n{r['source']}\n\n入口路径：\n{r['dest']}\n\n错误：{r.get('error', '无')}")

    def snapshot(self):
        c={k:v.get().strip() if k!='password' else v.get() for k,v in self.values.items()}
        if not c['host'] or not c['user']: raise ValueError('请填写 NAS 地址和 SSH 用户名。')
        c['port']=int(c['port'])
        if not 1<=c['port']<=65535: raise ValueError('端口应在 1～65535 之间。')
        for k in ('source','dest'):
            if not c[k].startswith('/') or c[k]=='/': raise ValueError('请填写 NAS 的绝对目录路径，且不能是根目录。')
            c[k]=pp.normpath(c[k])
        if excluded_path(c['source']) or excluded_path(c['dest']):
            raise ValueError('不能将回收站或其内部目录作为原始目录或入口目录。')
        if overlap(c['source'],c['dest']): raise ValueError('原始目录和入口目录不能相同或互相包含。')
        return c

    def start(self,fn):
        if self.busy:return
        self.busy=True
        for b in (self.scanbutton,self.runbutton,self.exportbutton): b.configure(state='disabled')
        def work():
            try: fn()
            except Exception as e:self.q.put(('error',str(e)))
            finally:self.q.put(('done',None))
        threading.Thread(target=work,daemon=True).start()

    def pump(self):
        try:
            while True:
                kind,data=self.q.get_nowait()
                if kind=='trust':
                    text,event,answer=data
                    answer.append(messagebox.askyesno('首次连接：核对 NAS 的 SSH 指纹',text));event.set()
                elif kind=='error':
                    self.status.set('操作未完成，请查看错误。');messagebox.showerror('操作未完成',data);self.write_log(data)
                elif kind=='log':self.write_log(data)
                elif kind=='status':self.status.set(data)
                elif kind=='rows':
                    self.rows,self.plan=data
                    self.tree.delete(*self.tree.get_children())
                    for i,r in enumerate(self.rows): self.tree.insert('','end',iid=str(i),values=(r['status'],r.get('bytes',''),r['rel'],r.get('newrel','')))
                elif kind=='done':
                    self.busy=False;self.scanbutton.configure(state='normal')
                    if self.rows:self.exportbutton.configure(state='normal')
                    if self.plan and not any(r['status']=='扫描错误' for r in self.rows):self.runbutton.configure(state='normal')
        except queue.Empty:pass
        self.root.after(100,self.pump)

    def write_log(self,s):
        self.log.configure(state='normal');self.log.insert('end',s+'\n');self.log.see('end');self.log.configure(state='disabled')

    def connect(self,c):
        checked(f'创建本机配置目录 {APP}', APP.mkdir, parents=True, exist_ok=True)
        client=paramiko.SSHClient()
        known=APP/'known_hosts'
        if known.exists():checked(f'读取本机 SSH 主机密钥 {known}', client.load_host_keys, str(known))
        outer=self
        class Trust(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self,client,hostname,key):
                import base64
                fp=base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                event=threading.Event();answer=[]
                outer.q.put(('trust',(f'地址：{hostname}\n类型：{key.get_name()}\n指纹：SHA256:{fp}\n\n请与 NAS 的主机指纹核对。确认信任并保存？',event,answer)))
                event.wait()
                if not answer[0]:raise RuntimeError('未信任 SSH 主机，已取消。')
                client.get_host_keys().add(hostname,key.get_name(),key);checked(f'保存本机 SSH 主机密钥 {known}', client.save_host_keys, str(known))
        client.set_missing_host_key_policy(Trust())
        try:
            client.connect(c['host'],port=c['port'],username=c['user'],password=c['password'] or None,timeout=20,auth_timeout=20,banner_timeout=20,allow_agent=False,look_for_keys=False)
            sf=checked('登录成功后打开 NAS SFTP 会话', LinkSFTPClient.from_transport, client.get_transport());sf.get_channel().settimeout(40)
            checked(f'保存本机连接设置 {APP / "settings.json"}', (APP/'settings.json').write_text, json.dumps({k:v for k,v in c.items() if k!='password'},ensure_ascii=False), encoding='utf-8')
            return client,sf
        except Exception:client.close();raise

    @staticmethod
    def safe_path(sf,path,create=False):
        current='/'
        for part in path.strip('/').split('/'):
            current=pp.join(current,part)
            try:a=checked(f'检查 NAS 目录 {current}', sf.lstat, current)
            except FileNotFoundError:
                if create:checked(f'创建 NAS 目录 {current}', sf.mkdir, current);a=checked(f'检查新目录 {current}', sf.lstat, current)
                else:continue
            if stat.S_ISLNK(a.st_mode):raise RuntimeError('路径中包含软链接，暂不处理：'+current)
            if not stat.S_ISDIR(a.st_mode):raise RuntimeError('目录路径被文件占用：'+current)

    def scan(self):
        try:c=self.snapshot()
        except Exception as e:messagebox.showerror('检查设置',str(e));return
        self.plan=None;self.rows=[];self.tree.delete(*self.tree.get_children())
        def work():
            self.q.put(('status','正在通过 SFTP 只读扫描……'))
            client,sf=self.connect(c);rows=[];dest_seen={};dirs=[c['source']];skipped=0
            try:
                self.safe_path(sf,c['source']);self.safe_path(sf,c['dest'])
                if not stat.S_ISDIR(checked(f'读取 NAS 原始目录 {c["source"]}', sf.stat, c['source']).st_mode):raise RuntimeError('源路径不是目录。')
                while dirs:
                    directory=dirs.pop()
                    try:entries=checked(f'列出 NAS 目录 {directory}', sf.listdir_attr, directory)
                    except Exception as e:
                        self.q.put(('log',str(e)))
                        rows.append(dict(status='扫描错误',rel=pp.relpath(directory,c['source']),source=directory,dest='',error=str(e)));continue
                    for a in entries:
                        if a.filename in ('.','..'):continue
                        if '/' in a.filename:raise RuntimeError('服务器返回异常文件名。')
                        source=pp.join(directory,a.filename);rel=pp.relpath(source,c['source'])
                        if excluded_path(rel):
                            skipped += 1
                            continue
                        if stat.S_ISDIR(a.st_mode):dirs.append(source);continue
                        if not stat.S_ISREG(a.st_mode):
                            rows.append(dict(status='跳过非普通文件',source=source,dest='',rel=rel));continue
                        newrel=target_relative(rel);dest=pp.join(c['dest'],newrel)
                        r=dict(status='待创建',source=source,dest=dest,rel=rel,newrel=newrel,bytes=len(a.filename.encode('utf-8')),size=a.st_size,mtime=a.st_mtime)
                        if dest in dest_seen:raise RuntimeError('目标名称冲突，已停止：'+dest)
                        dest_seen[dest]=source
                        try:
                            d=checked(f'检查 NAS 目标文件 {dest}', sf.lstat, dest)
                            r['status']='目标已存在（跳过）' if stat.S_ISREG(d.st_mode) else '目标冲突'
                        except FileNotFoundError:pass
                        rows.append(r)
                    self.q.put(('status',f'只读扫描中：已发现 {len(rows)} 个文件／项目'))
                supported=getattr(sf,'extensions',{}).get('hardlink@openssh.com')=='1'
                self.q.put(('log','SFTP 硬链接扩展：'+('服务声明支持 v1。' if supported else '服务未声明支持，当前连接仅可预览，不能创建硬链接。')))
                self.q.put(('log','已有目标一律跳过，不覆盖；SFTP v3 不提供文件编号，已有目标不宣称已验证为硬链接。'))
                rows.sort(key=lambda r:r['source'])
                pending=sum(r['status']=='待创建' for r in rows);conflicts=sum(r['status']=='目标冲突' for r in rows);errors=sum(r['status']=='扫描错误' for r in rows)
                self.q.put(('rows',(rows,c)))
                self.q.put(('status',f'预览完成：{len(rows)} 项，待创建 {pending}，目标冲突 {conflicts}，扫描错误 {errors}，排除回收站入口 {skipped}。没有修改 NAS 文件。'))
                self.q.put(('log','预览包含普通文件；自动排除 .Trash、.Trash-*、#recycle、@Recycle、$RECYCLE.BIN、.recycle、.recyclebin 及内部全部内容。跳过软链接，不创建空目录。'))
            finally:sf.close();client.close()
        self.start(work)

    def export(self):
        path=filedialog.asksaveasfilename(defaultextension='.csv',initialfile='硬链接预览.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        with open(path,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['状态','名称UTF8字节','原路径','入口路径','错误'])
            for r in self.rows:writer.writerow([r['status'],r.get('bytes',''),r['source'],r['dest'],r.get('error','')])
        self.write_log('已导出预览：'+path)

    def execute(self):
        try:c=self.snapshot()
        except Exception as e:messagebox.showerror('检查设置',str(e));return
        if not self.plan or any(c[k]!=self.plan[k] for k in c if k!='password'):
            messagebox.showinfo('请重新预览','连接或目录设置已改变，请重新扫描。');return
        rows=[dict(r) for r in self.rows if r['status']=='待创建']
        if any(excluded_path(r['source']) or excluded_path(r['dest']) for r in rows):
            messagebox.showerror('预览已失效','预览中含回收站路径，请重新扫描。');return
        if not rows:messagebox.showinfo('没有待创建项目','请先扫描，或所有文件已有对应入口。');return
        if not messagebox.askyesno('这是修改操作：批量创建硬链接',f"将在 NAS 上新增目录和 {len(rows)} 个硬链接。\n\n原始目录：{c['source']}\n入口目录：{c['dest']}\n\n不覆盖、不删除、不重命名，不自动复制。失败后停止，已创建项目保留。\n删除原入口后，硬链接仍会保留数据。\n\n确认执行？",default='no'):return
        def work():
            client,sf=self.connect(c);created=[]
            try:
                require_hardlink(sf)
                self.safe_path(sf,c['source']);self.safe_path(sf,c['dest'],True)
                for r in rows:
                    a=checked(f'读取 NAS 源文件 {r["source"]}', sf.lstat, r['source'])
                    if not stat.S_ISREG(a.st_mode) or a.st_size!=r['size'] or a.st_mtime!=r['mtime']:raise RuntimeError('源文件在预览后发生变化，请重新扫描：'+r['source'])
                    self.safe_path(sf,pp.dirname(r['source']))
                    self.safe_path(sf,pp.dirname(r['dest']),True)
                    create_hardlink(sf,r['source'],r['dest'])
                    created.append(r)
                    APP.mkdir(parents=True,exist_ok=True)
                    with (APP/'created.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps({**r,'host':c['host'],'port':c['port']},ensure_ascii=False)+'\n')
                    self.q.put(('status',f'服务器已确认创建 {len(created)} / {len(rows)} 个硬链接'))
                self.q.put(('log',f'完成：SFTP 服务已确认创建 {len(created)} 个硬链接。请用 WinSCP 查看目标目录，再让 Emby 扫描。'))
            finally:
                self.q.put(('log',f'本次已创建 {len(created)} 项；记录保存在本机 %LOCALAPPDATA%\\EmbyLinkStudio\\created.jsonl。重新扫描可核对现状。'))
                sf.close();client.close();self.plan=None
        self.start(work)

if __name__=='__main__':
    app=Studio()
    if '--self-test' in sys.argv:
        marker=Path(sys.argv[sys.argv.index('--self-test')+1])
        app.root.update()
        marker.write_text(json.dumps({'gui':'ok','ssh_library':paramiko.__version__,'version':VERSION}),encoding='utf-8')
        app.root.after(100,app.root.destroy)
    app.root.mainloop()
