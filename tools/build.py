#!/usr/bin/env python3
"""Build the Claude Desktop extension: dist/<name>-<version>.mcpb

    python tools/build.py

A .mcpb file is a zip archive. This script packs apps/server/ (without VERSION)
plus README.md, LICENSE and, if present, THIRD-PARTY.md from the project root.
It needs nothing but Python 3.8 or newer - no Node.js, no npx, no download.
The same sources always give the same file: entries are sorted and carry a
fixed timestamp.

Before packing it checks what Claude Desktop relies on: manifest.json and
VERSION name the same version, the entry point and every ${__dirname} path of
the start command exist, and the icon is a PNG inside the bundle.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "server"
DIST = ROOT / "dist"
ROOT_FILES = ("README.md", "LICENSE", "THIRD-PARTY.md")
# Left out: repository metadata, caches and what `mcpb pack` leaves out as well.
SKIP_FILES = ("VERSION", "Thumbs.db", ".DS_Store", ".git*", ".mcpbignore", ".env*",
              "*.log", "*.map", "*.mcpb", "*.pyc", "*.d.ts", "*.tsbuildinfo",
              "package-lock.json", "yarn.lock", "tsconfig.json")
SKIP_DIRS = ("__pycache__", ".git", "node_modules")
STAMP = (1980, 1, 1, 0, 0, 0)
PNG = b"\x89PNG\r\n\x1a\n"
DIRNAME = "${__dirname}/"


def fail(message: str) -> None:
    sys.exit(f"build: {message}")


def inside_app(relative: str) -> Path:
    """Resolve a bundle path; it must stay inside apps/server."""
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        fail(f"path must be relative, with / and without '..': {relative}")
    return APP / Path(*pure.parts)


def check(manifest: dict) -> None:
    version_file = APP / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    if not version or manifest.get("version") != version:
        fail(f"version differs: manifest.json {manifest.get('version')!r}, VERSION {version!r}")
    if not manifest.get("name"):
        fail("manifest.json has no name")
    server = manifest.get("server") or {}
    entry = server.get("entry_point", "")
    if not entry or not inside_app(entry).is_file():
        fail(f"entry_point not found in apps/server: {entry!r}")
    for arg in (server.get("mcp_config") or {}).get("args", []):
        if arg.startswith(DIRNAME) and not inside_app(arg[len(DIRNAME):]).is_file():
            fail(f"start command points to a missing file: {arg}")
    icon = manifest.get("icon")
    if icon:
        path = inside_app(icon)
        if not path.is_file() or not path.read_bytes().startswith(PNG):
            fail(f"icon missing or not a PNG: {icon}")


def collect() -> list[tuple[str, Path]]:
    entries = []
    for path in sorted(APP.rglob("*")):
        relative = path.relative_to(APP)
        if not path.is_file() or any(part in SKIP_DIRS for part in relative.parts):
            continue
        if any(fnmatch(path.name, pattern) for pattern in SKIP_FILES):
            continue
        entries.append((relative.as_posix(), path))
    for name in ROOT_FILES:
        if (ROOT / name).is_file():
            entries.append((name, ROOT / name))
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        fail("apps/server contains a file with the name of a root file (README.md, LICENSE ...)")
    return entries


def main() -> None:
    try:
        manifest = json.loads((APP / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail(f"cannot read apps/server/manifest.json: {error}")
    check(manifest)
    entries = collect()
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{manifest['name']}-{manifest['version']}.mcpb"
    with zipfile.ZipFile(target, "w") as bundle:
        for name, path in entries:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16  # regular file, rw-r--r--
            bundle.writestr(info, path.read_bytes(), compresslevel=9)
    data = target.read_bytes()
    print(f"{target.relative_to(ROOT).as_posix()}: {len(entries)} files, {len(data):,} bytes")
    print(f"sha256 {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
