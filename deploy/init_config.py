"""Create a deployment .env once, without displaying its random token."""
import argparse
import os
from pathlib import Path, PurePosixPath
import secrets


def create_config(directory, media_root):
    if not isinstance(media_root, str):
        raise ValueError("MEDIA_ROOT must be an absolute NAS path")
    path = PurePosixPath(media_root)
    if (not path.is_absolute() or str(path) == "/" or ".." in path.parts
            or any(char in media_root for char in "\r\n\0\"'$\\")
            or "ABSOLUTE/PATH" in media_root):
        raise ValueError("Use a real absolute NAS path without control or interpolation characters")
    directory = Path(directory)
    token = secrets.token_urlsafe(32)
    content = f'AGENT_TOKEN={token}\nMEDIA_ROOT="{media_root}"\nENABLE_METADATA_WRITES=false\n'
    target = directory / ".env"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--media-root", required=True, help="NAS host parent of originals and hardlinks")
    args = parser.parse_args()
    try:
        create_config(args.directory, args.media_root)
    except FileExistsError:
        parser.exit(1, "Existing .env preserved; no key was replaced.\n")
    except (OSError, ValueError):
        parser.exit(1, "Check the directory and NAS path. No token was printed.\n")
    print("Created private .env. Keep it local; do not upload it.")


if __name__ == "__main__":
    main()
