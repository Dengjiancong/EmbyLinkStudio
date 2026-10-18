"""Export a reviewed HEAD snapshot without Git history or local untracked files."""
import argparse
import io
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from link_rules import VERSION
from tools.check_public import ROOT, findings


def export_snapshot(output):
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT).returncode:
        raise ValueError("Commit reviewed tracked changes before exporting a snapshot")
    raw = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        entries = {}
        for info in source.infolist():
            if info.is_dir():
                continue
            data = source.read(info.filename)
            if findings(info.filename, data):
                raise ValueError("Snapshot privacy finding: " + info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Snapshot symlink: " + info.filename)
            entries[info.filename] = data
    if "LICENSE" not in entries:
        raise ValueError("Missing license")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    print(f"Reviewed source snapshot: {output.name}; no history included.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"EmbyLinkStudio-source-v{VERSION}.zip")
    args = parser.parse_args()
    export_snapshot(args.output)


if __name__ == "__main__":
    main()
