"""Pinned directory operations on POSIX; conservative checked-path fallback for Windows tests."""
import contextlib,os,stat
from pathlib import Path
PINNED=os.name=="posix" and os.open in os.supports_dir_fd and os.link in os.supports_dir_fd
def identity(info):return (info.st_dev,info.st_ino)
def snap(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
class LinkCreatedError(RuntimeError):
    created=True
def checked_directory(path):
    path=Path(path);current=Path(path.anchor)
    if not path.is_absolute() or ".." in path.parts:raise ValueError("Unsafe directory")
    for part in path.parts[1:]:
        current/=part;info=current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):raise ValueError("拒绝软链接或非目录："+str(current))
@contextlib.contextmanager
def directory(path,create=False,boundary=None):
    path=Path(path);boundary=Path(boundary) if boundary is not None else None
    if not path.is_absolute() or ".." in path.parts:raise ValueError("Unsafe directory")
    if not PINNED:
        current=Path(path.anchor)
        for part in path.parts[1:]:
            current/=part
            if not current.exists() and not current.is_symlink():
                if not create or boundary is None or not (current==boundary or boundary in current.parents):raise FileNotFoundError(str(current))
                current.mkdir()
            checked_directory(current)
        yield None;return
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open(path.anchor,flags)
    try:
        current=Path(path.anchor)
        for part in path.parts[1:]:
            current/=part
            try:new=os.open(part,flags,dir_fd=fd)
            except FileNotFoundError:
                if not create or boundary is None or not (current==boundary or boundary in current.parents):raise
                try:os.mkdir(part,0o755,dir_fd=fd)
                except FileExistsError:pass
                new=os.open(part,flags,dir_fd=fd)
            os.close(fd);fd=new
        yield fd
    finally:os.close(fd)
def ensure_tree(root,parent):
    root=Path(root);parent=Path(parent)
    if parent!=root and root not in parent.parents:raise ValueError("目录超出入口范围")
    with directory(parent,True,root):pass
def open_private(path,flags,mode=0o600):
    path=Path(path)
    with directory(path.parent) as parent:
        opts=flags|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)
        if parent is None:
            if path.is_symlink():raise ValueError("拒绝日志软链接")
            fd=os.open(path,opts,mode)
        else:fd=os.open(path.name,opts,mode,dir_fd=parent)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError("记录文件必须为非共享普通文件")
        return fd
    except Exception:os.close(fd);raise
@contextlib.contextmanager
def private_reader(path):
    fd=open_private(path,os.O_RDONLY)
    with os.fdopen(fd,"r",encoding="utf-8") as stream:yield stream
def link(src,dest):
    src=Path(src);dest=Path(dest)
    with directory(src.parent) as a,directory(dest.parent) as b:
        def read(path,parent):
            return path.lstat() if parent is None else os.stat(path.name,dir_fd=parent,follow_symlinks=False)
        before=read(src,a)
        if not stat.S_ISREG(before.st_mode):raise ValueError("原件不是普通文件")
        if a is None:os.link(src,dest,follow_symlinks=False)
        else:os.link(src.name,dest.name,src_dir_fd=a,dst_dir_fd=b,follow_symlinks=False)
        try:
            after=read(src,a);target=read(dest,b)
            if snap(before)!=snap(after) or not stat.S_ISREG(target.st_mode) or identity(before)!=identity(target) or target.st_nlink<2:
                raise ValueError("链接创建后原件或入口身份变化")
            # The visible paths must still refer to the pinned parents.
            if a is not None and identity(src.parent.stat())!=identity(os.fstat(a)):raise ValueError("原件目录位置变化")
            if b is not None and identity(dest.parent.stat())!=identity(os.fstat(b)):raise ValueError("入口目录位置变化")
            return target
        except Exception as error:raise LinkCreatedError("入口可能已创建，但核验未通过；保留现场，请只读复核："+str(error)) from error
def physical_overlap(a,b):
    """Only matching full directory identities imply overlap; device differences do not reject links."""
    a=Path(a);b=Path(b)
    def existing(path):
        if path.exists() and not path.is_symlink():
            value=path.stat()
            if value.st_ino:return identity(value)
        return None
    ar=existing(a);br=existing(b)
    if ar and any(existing(p)==ar for p in (b,*b.parents)):return True
    if br and any(existing(p)==br for p in (a,*a.parents)):return True
    return False
