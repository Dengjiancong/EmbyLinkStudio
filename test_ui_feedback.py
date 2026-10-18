"""GUI feedback regression: stale snapshots cannot appear pending."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import docker_client
class FeedbackTests(unittest.TestCase):
    def test_expired_preview_and_timestamp(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(docker_client,"APP",Path(folder)):
            app=docker_client.Studio()
            app.root.withdraw()
            try:
                app.rows=[dict(status="待创建",reason="",bytes=3,rel="a.mp4",newrel="a.mp4",source="a",dest="b")]
                app.mark_expired("创建已完成。")
                self.assertEqual(app.rows[0]["status"],"预览已过期")
                self.assertIn("预览已过期",app.preview_note.get())
                self.assertEqual(str(app.exportbtn["state"]),"disabled")
                app.logline("测试消息")
                self.assertRegex(app.log.get("1.0","end"),r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 测试消息")
            finally:app.root.destroy()

