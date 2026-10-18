"""Explicit title-only NFO maintenance: backup, identity guard, no truncate/rename/delete."""
import hashlib,os,stat
from pathlib import Path
from safe_fs import open_private,ensure_tree
from xml.etree import ElementTree as ET
MAX_NFO=1024*1024
def signature(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
def parse(raw):
    if b"<!doctype" in raw.replace(b"\x00",b"").lower() or b"<!entity" in raw.replace(b"\x00",b"").lower():raise ValueError("含 DTD/实体的 NFO 不自动处理")
    parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root=ET.fromstring(raw,parser=parser)
    if root.tag!="movie":raise ValueError("当前仅更新 movie 类型 NFO")
    return root
def transformed(raw,title):
    root=parse(raw)
    for tag in ("title","originaltitle"):
        elements=root.findall(tag)
        if len(elements)>1:raise ValueError("标题字段重复，需人工处理")
        element=elements[0] if elements else ET.SubElement(root,tag);element.text=title
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)
def read_private(path,flags=os.O_RDONLY):
    initial=Path(path).lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink!=1:raise ValueError("拒绝更新软链接、目录或与其他名称共用数据的 NFO")
    fd=open_private(path,flags)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError("拒绝更新软链接、目录或与其他名称共用数据的 NFO")
        if info.st_size>MAX_NFO:raise ValueError("NFO 超过1MiB，需人工处理")
        with os.fdopen(fd,"rb",closefd=False) as source:raw=source.read(MAX_NFO+1)
        if len(raw)>MAX_NFO or signature(os.fstat(fd))!=signature(info):raise ValueError("读取期间 NFO 发生变化")
        return fd,info,raw
    except Exception:os.close(fd);raise
def inspect(path,title):
    fd,info,raw=read_private(path)
    try:
        root=parse(raw);old=root.findtext("title","");original=root.findtext("originaltitle","")
        transformed(raw,title)
        return dict(old_title=old,old_original_title=original,new_title=title,before_sha=hashlib.sha256(raw).hexdigest(),nfo_snapshot=signature(info),status="已一致" if old==title and original==title else "待创建",reason="标题已一致" if old==title and original==title else ("originaltitle 不一致；保留其他字段，先备份" if old==title else "可更新 title/originaltitle；保留其他字段，先备份"))
    finally:os.close(fd)
def update(path,row,state,job):
    from nas_agent import safe_directory
    safe_directory(Path(path).parent)
    fd,info,raw=read_private(path,os.O_RDWR)
    try:
        if signature(info)!=tuple(row["nfo_snapshot"]) or hashlib.sha256(raw).hexdigest()!=row["before_sha"]:raise ValueError("NFO 与预览时不同，拒绝更新，请重新预览")
        payload=transformed(raw,row["new_title"])
        directory=Path(state)/"nfo-backups";ensure_tree(Path(state),directory)
        backup=directory/(job["job_id"]+"-"+hashlib.sha256(str(path).encode()).hexdigest()[:24]+".nfo")
        bfd=open_private(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(bfd,"wb",closefd=False) as target:target.write(raw);target.flush();os.fsync(bfd)
        finally:os.close(bfd)
        latest=os.fstat(fd);current=Path(path).lstat()
        if signature(latest)!=signature(info) or latest.st_nlink!=1 or not os.path.samestat(latest,current):raise ValueError("更新前文件身份或链接数变化，拒绝写入")
        # Padding after the XML root preserves validity without O_TRUNC/ftruncate.
        payload=payload+b" "*max(0,len(raw)-len(payload))
        job["metadata_started"]=job.get("metadata_started",0)+1
        os.lseek(fd,0,os.SEEK_SET)
        with os.fdopen(fd,"wb",closefd=False) as target:target.write(payload);target.flush();os.fsync(fd)
        job["metadata_started"]-=1
        return str(backup)
    finally:os.close(fd)
