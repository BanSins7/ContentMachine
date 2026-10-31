"""Marketing-local compatibility for Tesseract 5 Windows version banners.

The frozen copied GA12 OCR engine parses "tesseract 5.x", while official
UB Mannheim Windows builds print "tesseract v5.x". Normalize only the
CLI --version banner in the runner result; leave all OCR TSV bytes,
process commands, exit codes, and source/model SHA checks untouched.
"""
from __future__ import annotations

import re
import subprocess

from ._copied.group_a.extractors.tesseract_ocr import TesseractAdapter

_BANNER = re.compile(rb"(?im)^(tesseract[ \t]+)v(?=5\.)")


def run_tesseract_compat(cmd, **kwargs):
    """Subprocess API adapter with a narrowly scoped Tesseract v5 banner fix."""
    result = subprocess.run(cmd, **kwargs)
    if (not isinstance(cmd, (list, tuple))
            or len(cmd) != 2 or cmd[1] != "--version"):
        return result
    stdout = result.stdout
    stderr = result.stderr
    if isinstance(stdout, bytes):
        stdout = _BANNER.sub(rb"\g<1>", stdout, count=1)
    if isinstance(stderr, bytes):
        stderr = _BANNER.sub(rb"\g<1>", stderr, count=1)
    if stdout is result.stdout and stderr is result.stderr:
        return result
    return subprocess.CompletedProcess(
        args=result.args, returncode=result.returncode,
        stdout=stdout, stderr=stderr,
    )


def build_tesseract_adapter(*, binary: str, tessdata_dir: str,
                            expected_sha256: str) -> TesseractAdapter:
    return TesseractAdapter(
        binary=binary, tessdata_dir=tessdata_dir,
        expected_sha256=expected_sha256,
        runner=run_tesseract_compat,
    )
