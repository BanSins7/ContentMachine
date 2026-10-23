"""Offline JSON preview: python -m intelligence.collector.preview_cli input.json.

Input is fixture/structural preview ONLY; neither Query Validation publication
nor project/source authorization is established by the JSON file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contracts import CollectorError
from .planner import build_preview

MAX_INPUT_BYTES = 256 * 1024


def preview_document(raw: dict) -> dict:
    if type(raw) is not dict or set(raw) != {"query_set", "source_id", "limits"}:
        raise CollectorError(
            "invalid_preview_request",
            "Preview requires query_set, source_id and limits only",
        )
    return build_preview(raw["query_set"], source_id=raw["source_id"],
                         limits=raw["limits"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Video Collector preview (no API calls)")
    parser.add_argument("input", help="Path to a local JSON preview request")
    args = parser.parse_args(argv)
    try:
        path = Path(args.input)
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise CollectorError("input_too_large", "Preview input exceeds 256 KiB")
        raw = json.loads(path.read_text(encoding="utf-8"))
        output = preview_document(raw)
    except (CollectorError, OSError, UnicodeError, ValueError) as exc:
        code = exc.code if isinstance(exc, CollectorError) else "invalid_input"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "preview": output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
