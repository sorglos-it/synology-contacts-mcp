#!/usr/bin/env python3
"""Build the Claude Desktop extension: dist/<name>-<version>.mcpb

    python tools/build.py

A .mcpb file is a zip archive. This script packs apps/server/ (without VERSION)
plus README.md, LICENSE and, if present, THIRD-PARTY.md from the project root.
It needs nothing but Python 3.8 or newer - no Node.js, no npx, no download.
The same sources always give the same file: entries are sorted and carry a
fixed timestamp.

Before packing it checks what Claude Desktop relies on: manifest.json, VERSION
and package.json (if there is one) name the same version, the entry point and
every ${__dirname} path of the start command exist, the icon is a PNG inside
the bundle, and every package.json with dependencies has them installed next
to it in node_modules/.
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
# Left out: apps/server/VERSION, caches, command shims (.bin) and everything
# `mcpb pack` leaves out by default.
SKIP_FILES = ("*.pyc", ".DS_Store", "Thumbs.db", ".gitignore", ".mcpbignore", "*.log",
              ".env*", ".npmrc", ".yarnrc", ".eslintrc", ".editorconfig", ".prettierrc",
              ".prettierignore", ".eslintignore", ".nycrc", ".babelrc", ".pnp.*", "*.map",
              "npm-debug.log*", "yarn-debug.log*", "yarn-error.log*", "package-lock.json",
              "yarn.lock", "*.mcpb", "*.d.ts", "*.tsbuildinfo", "tsconfig.json")
SKIP_DIRS = ("__pycache__", ".git", ".npm", ".yarn", ".bin", ".cache",
             "receipts", "review-receipts", "tests")
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


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail(f"cannot read {path.relative_to(ROOT).as_posix()}: {error}")
    return {}


def check_versions(manifest: dict) -> None:
    version_file = APP / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    if not version or manifest.get("version") != version:
        fail(f"version differs: manifest.json {manifest.get('version')!r}, VERSION {version!r}")
    package = APP / "package.json"
    if package.is_file() and read_json(package).get("version") != version:
        fail(f"version differs: package.json {read_json(package).get('version')!r}, VERSION {version!r}")


def check_start(manifest: dict) -> None:
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


def check_dependencies() -> None:
    for package in sorted(APP.rglob("package.json")):
        if "node_modules" in package.relative_to(APP).parts:
            continue
        folder = package.parent
        for name in read_json(package).get("dependencies") or {}:
            if not (folder / "node_modules" / name / "package.json").is_file():
                where = folder.relative_to(ROOT).as_posix()
                fail(f"{name} is missing in {where}/node_modules - run: "
                     f"npm install --prefix {where} --omit=dev --ignore-scripts")


def collect() -> list[tuple[str, Path]]:
    entries = []
    for path in APP.rglob("*"):
        relative = path.relative_to(APP)
        if not path.is_file() or any(part in SKIP_DIRS for part in relative.parts):
            continue
        if relative.as_posix() == "VERSION" or any(fnmatch(path.name, p) for p in SKIP_FILES):
            continue
        entries.append((relative.as_posix(), path))
    for name in ROOT_FILES:
        if (ROOT / name).is_file():
            entries.append((name, ROOT / name))
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        fail("apps/server contains a file with the name of a root file (README.md, LICENSE ...)")
    return sorted(entries)


def main() -> None:
    manifest = read_json(APP / "manifest.json")
    check_versions(manifest)
    check_start(manifest)
    check_dependencies()
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
