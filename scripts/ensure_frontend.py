#!/usr/bin/env python3
"""Build ContentMachine's React interfaces when Node/npm is available.

Main React and native TikTok Carousel Studio have independent build markers,
so a successful main build never masks a missing/failed Carousel Studio build.
Missing Node/npm does not block the Python server; the studio route reports
that its independent frontend build is unavailable.
"""
from __future__ import annotations

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


def build(name: str, source: Path, output: Path, marker: Path, rev: str, npm: str) -> bool:
    if not (source / "package.json").is_file():
        return True
    if (rev and (output / "index.html").is_file() and marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == rev):
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
    output.mkdir(exist_ok=True, parents=True)
    marker.write_text(rev or "local", encoding="utf-8")
    print(f"{name}: React + TypeScript frontend built.")
    return True


def main() -> int:
    rev = revision()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("Node.js/npm not found: main uses legacy HTML fallback; Carousel Studio requires Node.js 22+.")
        return 0
    main_ok = build("Main", FRONTEND, DIST, MARKER, rev, npm)
    studio_ok = build("Carousel Studio", STUDIO_FRONTEND, STUDIO_DIST,
                      STUDIO_MARKER, rev, npm)
    # Preserve existing best-effort launch. A stale built file is never
    # described as newly built if the corresponding npm command failed.
    if not main_ok or not studio_ok:
        print("One or more React builds failed; inspect messages above.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
