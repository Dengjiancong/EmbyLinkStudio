"""Shared result labels; unknown completion must never be presented as success."""
def classify(job):
    effects=job.get("created",0)+job.get("metadata_created",0)+job.get("metadata_started",0)+job.get("metadata_updated",0)
    status=job.get("status","")
    if status in ("unconfirmed","unknown") or job.get("state_unknown"):return "unknown","状态未知"
    if not job.get("done"):return "running",("正在安全停止" if job.get("stop_requested") else "执行中")
    if status=="completed" and not job.get("error"):
        if job.get("processed",job.get("created",0))<job.get("total",0):return "unknown","状态未知"
        return "success","全部完成"
    if effects:return "partial",("部分完成，已安全停止" if status=="stopped" else "部分完成，需复核")
    return "none",("未创建文件，已安全停止" if status=="stopped" else "未创建文件，需检查错误")
def summary(job):
    code,label=classify(job)
    counts=f"硬链接 {job.get('created',0)}，NFO {job.get('metadata_created',0)}，更新 NFO {job.get('metadata_updated',0)}；已处理 {job.get('processed',job.get('created',0))} / {job.get('total',0)} 项"
    if code=="unknown":return label+"：NAS 可能仍在执行或已完成。请恢复查看或只读复核，不要直接重复提交。"
    return label+"："+counts+("；可能有未完整写入的 NFO，请只读检查" if job.get("metadata_started",0) else "")+("。可能已新增目录。" if code=="none" else "。")
