"""Read-only privacy checks; findings contain locations/categories, never values."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    ("private-ip", re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
    ("nas-user-path", re.compile(r"/(?:tmp/zfsv3/[^/\s]+/|sata\d+-)[a-zA-Z0-9]{10,}(?:/|\b)")),
    ("windows-user-path", re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!USER\b|PUBLIC\b|EXAMPLE\b|<)[^\\/\s\"']+", re.I)),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("credential-in-url", re.compile(r"https?://[^\s/:@]+:[^\s/@]+@")),
    ("assigned-secret", re.compile(r"(?im)^\s*(?:AGENT_TOKEN|QBIT_PASSWORD)\s*=\s*['\"]?([A-Za-z0-9_+/=-]{24,})")),
)
PRIVATE_NAMES = {"known_hosts", "settings.json", "agent_settings.json", "created.jsonl"}
EXAMPLE_PREFIXES = ("REPLACE", "EXAMPLE", "PLACEHOLDER")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def private_name(name):
    parts = Path(name).parts
    return (Path(name).name in PRIVATE_NAMES
            or (Path(name).name.startswith(".env") and Path(name).name != ".env.example")
            or any(part in {"state", ".venv", "__pycache__"} for part in parts))


def findings(name, data):
    result = []
    if private_name(name):
        result.append({"path": name, "line": 0, "category": "private-file"})
    if b"\0" in data:
        return result
    text = data.decode("utf-8", errors="replace")
    for category, pattern in PATTERNS:
        for match in pattern.finditer(text):
            if category == "assigned-secret" and match.group(1).startswith(EXAMPLE_PREFIXES):
                continue
            result.append({
                "path": name, "line": text.count("\n", 0, match.start()) + 1,
                "category": category,
            })
    return result


def scan(history=False):
    result = []
    if history:
        seen = set()
        for commit in git("rev-list", "--all").decode().splitlines():
            for item in git("ls-tree", "-rz", commit).split(b"\0"):
                if not item:
                    continue
                metadata, raw_name = item.split(b"\t", 1)
                mode, kind, blob = metadata.split()
                if kind != b"blob":
                    continue
                name = raw_name.decode("utf-8")
                key = (name, blob)
                if key in seen:
                    continue
                seen.add(key)
                for issue in findings(name, git("cat-file", "blob", blob.decode())):
                    result.append(dict(issue, commit=commit[:12]))
    else:
        for raw in git("ls-files", "-z").split(b"\0"):
            if raw:
                name = raw.decode("utf-8")
                path = ROOT / name
                if path.is_symlink():
                    result.append({"path": name, "line": 0, "category": "symlink"})
                elif path.is_file():
                    result.extend(findings(name, path.read_bytes()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = scan(args.history)
    report = {"scope": "history" if args.history else "tracked-working-tree", "findings": result,
              "limits": "Pattern check only; inspect untracked files, metadata, media and arbitrary secrets manually."}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Privacy check: {len(result)} finding(s); values suppressed.")
    for issue in result[:30]:
        print(f"{issue['path']}:{issue['line']}: {issue['category']}")
    return bool(result)


if __name__ == "__main__":
    raise SystemExit(main())
