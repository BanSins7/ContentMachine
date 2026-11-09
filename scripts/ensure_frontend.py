#!/usr/bin/env python3
"""Build ContentMachine's React interfaces when Node/npm is available.

Main React and native TikTok Carousel Studio have independent build markers,
so a successful main build never masks a missing/failed Carousel Studio build.
Missing Node/npm does not block the Python server; the studio route reports
that its independent frontend build is unavailable.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = ROOT / "site" / "react-dist"
MARKER = DIST / ".built-commit"
STUDIO_FRONTEND = ROOT / "carousel-studio"
STUDIO_DIST = ROOT / "site" / "carousel-studio"
STUDIO_MARKER = STUDIO_DIST / ".built-commit"
# These HTML templates are bundled by the React Vite entrypoint as raw source.
MAIN_TEMPLATES = (
    ROOT / "site" / "index.html",
    ROOT / "site" / "sre-studio.html",
    ROOT / "intelligence" / "group_a" / "site" / "group-a.html",
)


def source_fingerprint(source: Path, extras: tuple[Path, ...] = ()) -> str:
    """Track uncommitted edits as well as HEAD; never hash node_modules or build output."""
    digest = hashlib.sha256()
    tracked: list[tuple[str, Path]] = []
    for base, directories, files in os.walk(source):
        directories[:] = sorted(
            name for name in directories
            if name not in {"node_modules", ".git", "dist", ".vite", "coverage"}
        )
        for name in sorted(files):
            file = Path(base) / name
            if not file.is_symlink() and file.suffix in {
                ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".mjs", ".cjs",
            }:
                tracked.append((file.relative_to(source).as_posix(), file))
    tracked.extend((f"external/{idx}/{file.name}", file) for idx, file in enumerate(extras))
    for relative, file in sorted(tracked):
        digest.update(relative.encode("utf-8") + b"\\0")
        digest.update(file.read_bytes() if file.is_file() else b"<missing>")
    return digest.hexdigest()


def revision() -> str:
    git = shutil.which("git")
    if not git:
        return ""
    try:
        run = subprocess.run(
            [git, "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if run.returncode == 0:
            return run.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def build(name: str, source: Path, output: Path, marker: Path, rev: str, npm: str,
          extras: tuple[Path, ...] = ()) -> bool:
    if not (source / "package.json").is_file():
        return True
    fingerprint_marker = output / ".built-sources-sha256"
    fingerprint = source_fingerprint(source, extras)
    marker_value = rev or "local"
    if ((output / "index.html").is_file() and marker.is_file()
            and fingerprint_marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == marker_value
            and fingerprint_marker.read_text(encoding="utf-8").strip() == fingerprint):
        return True
    try:
        install = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"], cwd=source,
            timeout=240, check=False,
        )
        if install.returncode:
            print(f"{name}: npm install failed; build unavailable.", file=sys.stderr)
            return False
        completed = subprocess.run(
            [npm, "run", "build"], cwd=source, timeout=240, check=False,
        )
        if completed.returncode:
            print(f"{name}: TypeScript/Vite build failed.", file=sys.stderr)
            return False
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"{name}: build unavailable: {exc}", file=sys.stderr)
        return False
    if not (output / "index.html").is_file():
        print(f"{name}: build command succeeded but no index.html was produced.", file=sys.stderr)
        return False
    output.mkdir(exist_ok=True, parents=True)
    # npm install may change package-lock.json; capture final inputs.
    fingerprint_marker.write_text(source_fingerprint(source, extras), encoding="utf-8")
    marker.write_text(marker_value, encoding="utf-8")
    print(f"{name}: React + TypeScript frontend built.")
    return True


def main() -> int:
    rev = revision()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("Node.js/npm not found: main uses legacy HTML fallback; Carousel Studio requires Node.js 22+.")
        return 0
    main_ok = build("Main", FRONTEND, DIST, MARKER, rev, npm, MAIN_TEMPLATES)
    studio_ok = build("Carousel Studio", STUDIO_FRONTEND, STUDIO_DIST,
                      STUDIO_MARKER, rev, npm)
    # Preserve existing best-effort launch. A stale built file is never
    # described as newly built if the corresponding npm command failed.
    if not main_ok or not studio_ok:
        print("One or more React builds failed; inspect messages above.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
