"""Headless Instagram Top-100 downloader using existing ContentMachine workers."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from intelligence.instagram_top100.service import RunManager


def main():
    parser = argparse.ArgumentParser(description="Download top Reels per Instagram account since Nov 1.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--accounts", help="Comma/whitespace-separated Instagram handles")
    source.add_argument("--file", type=Path, help="UTF-8 text file containing handles")
    parser.add_argument("--scan-limit", type=int, default=500)
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--confirm-rights", action="store_true")
    args = parser.parse_args()
    handles = args.accounts if args.accounts is not None else args.file.read_text(encoding="utf-8")
    manager = RunManager()
    run_id = manager.start(handles, args.scan_limit, paid_confirmed=args.confirm_paid,
                           rights_confirmed=args.confirm_rights)["id"]
    print("Run: " + run_id, flush=True)
    try:
        while True:
            run = manager.get(run_id)
            print(json.dumps({"status": run["status"],
                "accounts": [{k: row[k] for k in ("username", "status", "selected", "downloaded", "cached", "failed")}
                             for row in run["accounts"]]}, ensure_ascii=False), flush=True)
            if run["status"] != "running":
                break
            time.sleep(5)
    except KeyboardInterrupt:
        manager.cancel(run_id)
        print("Cancellation requested.", flush=True)


if __name__ == "__main__":
    main()
