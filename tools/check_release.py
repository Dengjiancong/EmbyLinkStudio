"""Validate current-version NAS bundles and generate checksums for candidates."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from link_rules import VERSION
from package_deploy import manifest
from tools.check_public import findings


def verify_archive(path, incremental=False):
    expected = set(manifest(incremental)) | {"manifest.json"}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError("Unexpected or missing archive entries")
        record = json.loads(archive.read("manifest.json"))
        if record.get("version") != VERSION or set(record.get("sha256", {})) != expected - {"manifest.json"}:
            raise ValueError("Invalid manifest")
        for name in names:
            data = archive.read(name)
            if name != "manifest.json" and hashlib.sha256(data).hexdigest() != record["sha256"][name]:
                raise ValueError(f"Checksum mismatch: {name}")
            if findings(name, data):
                raise ValueError(f"Private data requires review: {name}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    paths = [
        args.directory / f"EmbyLinkStudio-NAS-v{VERSION}.zip",
        args.directory / f"EmbyLinkStudio-NAS-update-v{VERSION}.zip",
    ]
    for path, incremental in zip(paths, (False, True)):
        verify_archive(path, incremental)
    source = args.directory / f"EmbyLinkStudio-source-v{VERSION}.zip"
    if source.exists():
        with zipfile.ZipFile(source) as archive:
            entries = archive.namelist()
            if len(entries) != len(set(entries)) or "LICENSE" not in entries:
                raise ValueError("Invalid source snapshot")
            for name in entries:
                if name.startswith(".git/") or findings(name, archive.read(name)):
                    raise ValueError("Private source snapshot entry")
        paths.append(source)
    executable = args.directory / f"EmbyLinkStudio-v{VERSION}.exe"
    if executable.exists():
        paths.append(executable)
    checksums = "\n".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in paths) + "\n"
    (args.directory / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    print(f"Verified {len(paths)} current-version candidates. Older dist files are not approved for publication.")


if __name__ == "__main__":
    main()
