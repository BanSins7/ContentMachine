#!/usr/bin/env python3
"""Build the local React distribution if Node is installed.

Best effort: a missing Node/npm installation never blocks the Python backend.
A previous distribution is not trusted as current after a checkout change.
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


def main() -> int:
    if not (FRONTEND / "package.json").is_file():
        return 0
    git = shutil.which("git")
    revision = ""
    if git:
        run = subprocess.run([git, "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=False)
        if run.returncode == 0:
            revision = run.stdout.strip()
    if revision and (DIST / "index.html").is_file() and MARKER.is_file():
        if MARKER.read_text(encoding="utf-8").strip() == revision:
            return 0

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("Node.js/npm not found; using existing legacy interface.")
        return 0

    try:
        install = subprocess.run([npm, "install", "--no-audit", "--no-fund"],
                                 cwd=FRONTEND, timeout=180, check=False)
        if install.returncode:
            print("npm dependencies unavailable; using legacy interface.", file=sys.stderr)
            return 0
        build = subprocess.run([npm, "run", "build"],
                               cwd=FRONTEND, timeout=180, check=False)
        if build.returncode:
            print("React build failed; using legacy interface.", file=sys.stderr)
            return 0
    except (OSError, subprocess.SubprocessError) as exc:
        print("React build unavailable: " + str(exc), file=sys.stderr)
        return 0
    DIST.mkdir(exist_ok=True, parents=True)
    MARKER.write_text(revision or "local", encoding="utf-8")
    print("React + TypeScript frontend built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
