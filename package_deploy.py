"""Build public archives from an allowlist, never from local configuration."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile
from link_rules import VERSION

ROOT = Path(__file__).resolve().parent
NAS_MODULES = (
    "nas_agent.py", "link_rules.py", "delete_guard.py", "region_service.py",
    "media_features.py", "job_status.py", "qbit_reader.py", "nfo_editor.py",
    "advanced_service.py", "safe_fs.py",
)
names = NAS_MODULES  # Compatibility with earlier local verification tools.
RELEASE_GUIDE = "v0.11使用说明.md"


def manifest(incremental=False):
    entries = {f"app/{name}": f"src/{name}" for name in NAS_MODULES}
    entries.update({
        "LICENSE": "LICENSE",
        "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
        "部署说明.md": "docs/guides/部署说明.md",
        RELEASE_GUIDE: "docs/guides/" + RELEASE_GUIDE,
    })
    if incremental:
        entries.update({
            "config/regions.example.json": "deploy/config/regions.json",
            "config/regions.qbit.example.json": "deploy/config/regions.qbit.example.json",
            "docker-compose.regions.example.yml": "deploy/docker-compose.yml",
        })
    else:
        entries.update({
            "docker-compose.yml": "deploy/docker-compose.yml",
            ".env.example": "deploy/.env.example",
            "init_config.py": "deploy/init_config.py",
            "config/regions.json": "deploy/config/regions.json",
            "config/regions.qbit.example.json": "deploy/config/regions.qbit.example.json",
        })
    return entries


def build_archive(output, entries, root=ROOT):
    """Ignore stale dist folders and reject symlinked release inputs."""
    root = Path(root).resolve()
    payloads = {}
    for archive_name, relative in entries.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Invalid release input: {relative}")
        if any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
            raise ValueError(f"Symlinked release input: {relative}")
        payloads[archive_name] = path.read_bytes()
    checks = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    payloads["manifest.json"] = (json.dumps(
        {"version": VERSION, "sha256": checks}, ensure_ascii=False, indent=2
    ) + "\n").encode("utf-8")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            archive.writestr(name, data)
    return output


def build_bundles(output_dir=None):
    output_dir = Path(output_dir) if output_dir else ROOT / "dist"
    return [
        build_archive(output_dir / f"EmbyLinkStudio-NAS-v{VERSION}.zip", manifest()),
        build_archive(output_dir / f"EmbyLinkStudio-NAS-update-v{VERSION}.zip", manifest(True)),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    for path in build_bundles(args.output_dir):
        print(f"Public bundle: {path.name} (no credentials or state)")


if __name__ == "__main__":
    main()
