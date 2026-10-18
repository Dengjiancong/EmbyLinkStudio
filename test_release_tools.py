"""Release tooling tests use disposable files and never read real credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import docker_client
import package_deploy
from deploy.init_config import create_config
from link_rules import VERSION
from tools.check_public import findings
from tools.check_release import verify_archive


class ReleaseToolsTests(unittest.TestCase):
    def test_import_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "link_rules.py").write_text("VERSION='test'\n")
            (root / "package_deploy.py").write_bytes(Path(package_deploy.__file__).read_bytes())
            process = subprocess.run(
                [sys.executable, "-c", "import package_deploy"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse((root / "dist").exists())
            self.assertFalse((root / "deploy").exists())

    def test_public_bundle_ignores_stale_private_directory_and_local_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stale = root / f"EmbyLinkStudio-NAS-v{VERSION}"
            stale.mkdir()
            (stale / ".env").write_text("private-old-key")
            (root / ".env").write_text("private-local-key")
            archives = package_deploy.build_bundles(root)
            for index, path in enumerate(archives):
                self.assertTrue(verify_archive(path, bool(index)))
                with zipfile.ZipFile(path) as archive:
                    self.assertNotIn(".env", archive.namelist())
                    self.assertNotIn(b"private-local-key", b"".join(archive.read(n) for n in archive.namelist()))
                    self.assertFalse(any(n.startswith("state/") for n in archive.namelist()))
            self.assertEqual((stale / ".env").read_text(), "private-old-key")

    def test_manifest_detects_modified_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = package_deploy.build_bundles(folder)[0]
            with zipfile.ZipFile(path) as archive:
                contents = {name: archive.read(name) for name in archive.namelist()}
            contents["app/link_rules.py"] += b"\n# unexpected edit\n"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in contents.items():
                    archive.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "Checksum"):
                verify_archive(path)

    def test_archive_extra_private_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = package_deploy.build_bundles(folder)[0]
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr(".env", "untrusted")
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                verify_archive(path)

    def test_release_input_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.txt").write_text("safe")
            with self.assertRaises(ValueError):
                package_deploy.build_archive(root / "test.zip", {"entry": "../outside"}, root)

    def test_init_does_not_replace_existing_key_or_print_it(self):
        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                target = create_config(folder, "/storage/media")
            content = target.read_text()
            token = content.splitlines()[0].split("=", 1)[1]
            self.assertGreaterEqual(len(token), 24)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("ENABLE_METADATA_WRITES=false", content)
            with self.assertRaises(FileExistsError):
                create_config(folder, "/storage/other")
            self.assertEqual(target.read_text(), content)
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_init_refuses_interpolated_or_non_absolute_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            for path in ("/", "relative", "/a/../b", "/a\nb", "/a\0b", "/a$B", '/a"b', "/ABSOLUTE/PATH/TO/MEDIA"):
                with self.subTest(path=repr(path)), self.assertRaises(ValueError):
                    create_config(folder, path)
            self.assertFalse((Path(folder) / ".env").exists())

    def test_privacy_findings_never_contain_matched_values(self):
        sample = "AGENT_TOKEN=" + "synthetic-private-value-1234567890"
        issues = findings("example.txt", sample.encode())
        self.assertTrue(issues)
        self.assertNotIn("synthetic-private", json.dumps(issues))
        self.assertEqual(findings(".env", b"")[0]["category"], "private-file")
        self.assertFalse(findings(".env.example", b"AGENT_TOKEN=REPLACE_WITH_RANDOM_KEY_AT_LEAST_24_CHARACTERS"))

    def test_client_uses_shared_version(self):
        self.assertEqual(docker_client.CLIENT_VERSION, VERSION)


if __name__ == "__main__":
    unittest.main()
