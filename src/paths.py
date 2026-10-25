"""Portable validation of untrusted Git paths, including Windows edge cases."""

import re
from pathlib import Path, PurePosixPath


def safe_relative_path(filename: str) -> str:
    parts = filename.split("/")
    if (
        not filename
        or "\\" in filename
        or PurePosixPath(filename).is_absolute()
        or any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts)
        or any(ord(c) < 32 or c in ':*?"<>|' for c in filename)
    ):
        raise ValueError("Unsafe repository path")
    for part in parts:
        if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0]):
            raise ValueError("Reserved repository path")
    return filename


def write_sources(root: Path, files: list[dict]) -> None:
    # Callers place this mount beneath a private TemporaryDirectory (0700).
    # The mount itself must be readable by the container's unprivileged user.
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    root = root.resolve()
    seen: set[str] = set()
    for item in files:
        name = safe_relative_path(item["filename"])
        if name.casefold() in seen:
            raise ValueError("Case-colliding repository paths")
        seen.add(name.casefold())
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Repository path escapes review directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["raw_content"], encoding="utf-8")
        target.chmod(0o644)
        parent = target.parent
        while parent != root:
            parent.chmod(0o755)
            parent = parent.parent
