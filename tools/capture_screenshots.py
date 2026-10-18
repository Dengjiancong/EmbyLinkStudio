"""Capture the real Tk UI under Linux/Xvfb using synthetic, isolated data only.

No production UI code is changed. This helper never loads the operator's saved
settings and does not connect to a NAS. Pillow is an optional screenshot-only
dependency; it is not required to run EmbyLinkStudio.
"""
from pathlib import Path
import argparse
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def capture(window, path):
    from PIL import ImageGrab
    window.update_idletasks()
    window.update()
    time.sleep(0.4)
    left, top = window.winfo_rootx(), window.winfo_rooty()
    width, height = window.winfo_width(), window.winfo_height()
    if width < 600 or height < 400:
        raise RuntimeError("Screenshot window was not laid out")
    ImageGrab.grab(
        bbox=(left, top, left + width, top + height),
        xdisplay=os.environ["DISPLAY"],
    ).save(path, optimize=True)


def main():
    if sys.platform != "linux" or not os.environ.get("DISPLAY"):
        raise SystemExit("Run this capture-only helper under Linux/Xvfb.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/screenshots")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import tkinter as tk
    import tkinter.font as font
    import docker_client
    from link_rules import paired_target_relative

    with tempfile.TemporaryDirectory(prefix="embylink-demo-") as temporary, patch.object(
        docker_client, "APP", Path(temporary)
    ), patch("client_api.AgentClient.call", side_effect=RuntimeError("Screenshot demo has no network access")):
        app = docker_client.Studio()
        try:
            app.root.title("EmbyLinkStudio · 示例数据展示")
            app.root.geometry("1580x1020+20+20")
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                font.nametofont(name).configure(family="Noto Sans CJK SC", size=10)
            app.url.set("http://nas.example:8787")
            app.token.set("")
            app.task_name.set("示例作者收藏")
            app.region_label.set("示例视频收藏 [demo]")
            app.area.configure(values=("示例视频收藏 [demo]",))
            app.source_rel.set("示例作者")
            app.dest_rel.set("示例作者")
            app.source_map.set("原件位置（示例）：/storage/media/originals/示例作者 → /media/originals/示例作者")
            app.dest_map.set("硬链接位置（示例）：/storage/media/hardlinks/示例作者 → /media/hardlinks/示例作者")
            long_title = "秋日特别企划与幕后花絮・完整版・高清收藏・原始标题保留方便检索・" + "视频特别篇" * 12
            specifications = [
                ("待创建", long_title + ".mp4", "超出名称上限，保留可读标题并追加稳定编号"),
                ("待创建", long_title + ".jpg", "与同主名视频共用短主名，方便封面配对"),
                ("已有硬链接", "春日特别篇.mp4", "示例：入口已存在，不重复创建"),
                ("暂缓处理", "正在下载的特别篇.mp4", "示例：文件仍在变化，稍后再处理"),
                ("目标冲突", "同名文件.mp4", "示例：目标已有其他文件，保留不覆盖"),
                ("待创建", "幕后花絮.mp4", "名称保持原样"),
            ]
            app.rows = []
            for status, relative, reason in specifications:
                new_relative = paired_target_relative(relative, 240, (".mp4", ".jpg"))
                app.rows.append({
                    "status": status, "reason": reason, "bytes": len(relative.encode()),
                    "rel": relative, "newrel": new_relative, "action": "hardlink",
                    "source": "/media/originals/示例作者/" + relative,
                    "dest": "/media/hardlinks/示例作者/" + new_relative,
                })
            app.render()
            app.status.set("示例界面：数据全部为合成样例，未连接实际 NAS，未修改任何媒体。")
            app.preview_note.set("示例预览：6 项；待创建 3、已有硬链接 1、暂缓 1、冲突 1。筛选仅影响显示。")
            app.progress_text.set("当前展示只读预览；真实创建仍需新鲜预览和明确确认。")
            app.tree.selection_set("2")
            capture(app.root, args.output_dir / "preview.png")

            entry = {
                "exists": True, "device": 2049, "inode": 208801, "links": 2,
                "size": 2147483648,
            }
            result = {
                "shared_data": True, "checked_at": time.time(),
                "source": dict(entry, path="/media/originals/示例作者/春日特别篇.mp4"),
                "destination": dict(entry, path="/media/hardlinks/示例作者/春日特别篇.mp4"),
                "source_display": "/示例存储/originals/示例作者/春日特别篇.mp4",
                "destination_display": "/示例存储/hardlinks/示例作者/春日特别篇.mp4",
            }
            app.show_verification(result)
            dialog = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            dialog.title("硬链接核验 · 合成数据示例")
            dialog.geometry("1180x640+20+20")
            capture(dialog, args.output_dir / "verification.png")
        finally:
            app.root.destroy()
    print("Captured two real UI views using synthetic data only.")


if __name__ == "__main__":
    main()
