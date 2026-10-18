"""Test source export boundaries without reading or changing actual Git history."""
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from tools.source_snapshot import export_snapshot


class SourceSnapshotTests(unittest.TestCase):
    def archive(self, files, symlink=False):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, body in files.items():
                info = zipfile.ZipInfo(name)
                if symlink and name == "link":
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                archive.writestr(info, body)
            archive.comment = b"private-history-metadata"
        return data.getvalue()

    def export(self, path, raw, dirty=False):
        with patch("tools.source_snapshot.subprocess.run", return_value=SimpleNamespace(returncode=int(dirty))), patch(
            "tools.source_snapshot.subprocess.check_output", return_value=raw
        ):
            return export_snapshot(path)

    def test_clean_export_has_no_git_comment_or_untracked_files(self):
        raw = self.archive({"LICENSE": "MIT", "app.py": "print('example')"})
        with tempfile.TemporaryDirectory() as folder:
            output = self.export(Path(folder) / "source.zip", raw)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"LICENSE", "app.py"})
                self.assertEqual(archive.comment, b"")

    def test_private_tracked_file_prevents_export(self):
        raw = self.archive({"LICENSE": "MIT", ".env": "private-value"})
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "source.zip"
            with self.assertRaisesRegex(ValueError, "privacy"):
                self.export(output, raw)
            self.assertFalse(output.exists())

    def test_dirty_tracked_tree_cannot_be_exported_as_reviewed_head(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "source.zip"
            with self.assertRaisesRegex(ValueError, "Commit"):
                self.export(output, b"", dirty=True)
            self.assertFalse(output.exists())

    def test_symlink_prevents_export(self):
        raw = self.archive({"LICENSE": "MIT", "link": "/outside"}, symlink=True)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.export(Path(folder) / "source.zip", raw)


if __name__ == "__main__":
    unittest.main()
