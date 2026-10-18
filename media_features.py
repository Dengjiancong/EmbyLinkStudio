"""Download heuristics and add-only NFO title generation; no media writes here."""
import os,time
from pathlib import Path
from xml.etree import ElementTree as ET
VIDEO_EXTS={".mp4",".mkv",".avi",".mov",".wmv",".m4v",".ts",".m2ts",".flv",".webm",".mpg",".mpeg",".vob"}
TEMP_SUFFIXES=(".part",".partial",".crdownload",".download",".tmp",".!qb",".!ut",".aria2",".bc!")
def temporary(name):
    return name.lower().endswith(TEMP_SUFFIXES) or name.lower().startswith(".~")
def validate_options(settle_seconds,write_nfo):
    if type(settle_seconds) is not int or not 0<=settle_seconds<=86400:raise ValueError("静置时间必须为0～86400秒")
    if type(write_nfo) is not bool:raise ValueError("完整标题选项必须为布尔值")
def readiness(directory,settle_seconds,observations,entries=None):
    """No writes; mtime/size heuristics cannot prove a downloader has finished."""
    now=time.time();reason=""
    if entries is None:
        with os.scandir(directory) as iterator:entries=list(iterator)
    for entry in entries:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):continue
        if temporary(entry.name):return "同目录存在下载临时文件，整组暂缓"
        info=entry.stat(follow_symlinks=False)
        if Path(entry.name).suffix.lower() not in VIDEO_EXTS|{".jpg",".jpeg",".png",".nfo",".srt",".ass",".vtt"}:continue
        signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        old=observations.get(entry.path)
        observed=min(now,info.st_mtime) if old is None else (now if old[0]!=signature else old[1])
        observations[entry.path]=(signature,observed)
        if info.st_size==0:reason="同目录存在空媒体或配套文件，整组暂缓"
        elif settle_seconds and (now-info.st_mtime<settle_seconds or (old is not None and now-observed<settle_seconds)):
            reason="近期修改或两次扫描间发生变化，整组暂缓；静置后重新扫描"
    return reason
def title_xml(title):
    root=ET.Element("movie")
    ET.SubElement(root,"title").text=title
    ET.SubElement(root,"originaltitle").text=title
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)
def nfo_rows(rows,destination):
    planned={r["dest"].casefold() for r in rows};extra=[]
    eligible=[r for r in rows if Path(r["source"]).suffix.lower() in VIDEO_EXTS and r["status"] in ("待创建","已有硬链接")]
    counts={}
    for row in eligible:
        key=str(Path(row["dest"]).with_suffix(".nfo")).casefold();counts[key]=counts.get(key,0)+1
    directory_names={}
    for row in eligible:
        path=Path(row["dest"]).with_suffix(".nfo");key=str(path).casefold()
        if key in planned:continue
        planned.add(key)
        if path.parent.exists():
            if path.parent not in directory_names:directory_names[path.parent]={p.name.casefold():p for p in path.parent.iterdir()}
            path=directory_names[path.parent].get(path.name.casefold(),path)
        exists=path.exists() or path.is_symlink()
        status="已有元数据" if exists else "待创建"
        reason="已有 NFO，保留不覆盖；标题未改写" if exists else "新增 NFO，用原始视频完整文件主名作为 Emby 标题"
        if not exists and counts[key]>1:
            status="元数据冲突";reason="多个视频映射到同一个 NFO，请先人工确认标题，不自动生成"
        elif not exists and row.get("deferred_reason"):
            status="暂缓处理";reason=row["deferred_reason"]
        extra.append(dict(status=status,reason=reason,action="nfo",source=row["source"],dest=str(path),rel=row["rel"],newrel=path.relative_to(destination).as_posix(),bytes=row["bytes"],snapshot=row["snapshot"],media_dest=row["dest"],title=Path(row["source"]).stem))
    return extra
