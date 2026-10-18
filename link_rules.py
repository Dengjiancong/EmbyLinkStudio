"""Deterministic names; preserve readable originals below a UTF-8 byte limit."""
import hashlib
import posixpath as pp
import re
VERSION="0.11.1"
DEFAULT_NAME_BYTES=240
def excluded_path(path):
    names={".trash","#recycle","@recycle","$recycle.bin",".recycle",".recyclebin"}
    return any(p.casefold() in names or p.casefold().startswith(".trash-") for p in path.split("/") if p)
def validate_relative(rel):
    if not rel or rel.startswith("/") or any(p in ("",".","..") for p in rel.split("/")):
        raise ValueError("Unsafe relative path")
    return rel
def validate_budget(budget):
    if type(budget) is not int or not 80<=budget<=240:
        raise ValueError("Name limit must be an integer between 80 and 240 UTF-8 bytes")
    return budget
def short_component(name,budget=DEFAULT_NAME_BYTES):
    if len(name.encode())<=budget:return name
    digest=hashlib.sha256(name.encode()).hexdigest()[:8]
    dates=re.findall(r"(?<!\d)(20\d{2}[-_.]\d{1,2}[-_.]\d{1,2})(?!\d)",name)
    suffix=(" "+dates[-1] if dates else "")+" ["+digest+"]"
    return name.encode()[:budget-len(suffix.encode())].decode("utf-8","ignore").rstrip()+suffix
def previous_target_relative(rel,budget=DEFAULT_NAME_BYTES):
    validate_relative(rel);validate_budget(budget)
    parts=rel.split("/");stem,ext=pp.splitext(parts[-1])
    # Shared reserve keeps normal MP4/JPG/JPEG/SRT/NFO stems identical.
    reserve=max(8,len(ext.encode()))
    if reserve>32:raise ValueError("Unusually long extension; manual review required")
    filename=parts[-1] if len(parts[-1].encode())<=budget else short_component(stem,budget-reserve)+ext
    return "/".join([short_component(x,budget) for x in parts[:-1]]+[filename])
def target_relative(rel,budget=DEFAULT_NAME_BYTES):
    return previous_target_relative(rel,budget)

def paired_target_relative(rel,budget,extensions):
    parts=rel.split("/");stem,ext=pp.splitext(parts[-1])
    extensions=tuple(extensions)
    if max((len((stem+x).encode()) for x in extensions),default=len(parts[-1].encode()))<=budget:
        return target_relative(rel,budget)
    reserve=max(8,max((len(x.encode()) for x in extensions),default=8))
    if reserve>32:raise ValueError("扩展名过长，需要人工检查")
    name=short_component(stem,budget-reserve)+ext
    return "/".join([short_component(x,budget) for x in parts[:-1]]+[name])

def legacy_target_relative(rel):
    validate_relative(rel)
    def old(name,budget):
        if len(name.encode())<=budget:return name
        suffix="_"+hashlib.sha256(name.encode()).hexdigest()[:16]
        return name.encode()[:budget-len(suffix)].decode("utf-8","ignore")+suffix
    parts=rel.split("/");stem,ext=pp.splitext(parts[-1])
    return "/".join([old(x,80) for x in parts[:-1]]+[old(stem,80)+old(ext,32)])
