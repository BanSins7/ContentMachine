"""M16 standalone offline/local-only embedding health command.

Exit 0 ONLY after actual repeatable pinned ONNX inference on CPU.
Exit 3 for BLOCKED (missing asset, runtime or digest mismatch), 2 for invalid
JSON arguments. No HuggingFace hub, cloud embedding or credential lookup.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .local_embedding import probe_local_embedding_model


def main(argv=None):
    parser=argparse.ArgumentParser(description="SRE M16 pinned CPU ONNX real-model preflight")
    parser.add_argument("--model-dir",required=True,help="existing trusted local model directory")
    parser.add_argument("--pins-file",required=True,help="operator-pinned JSON model/revision/SHA descriptor")
    args=parser.parse_args(argv)
    try:
        with Path(args.pins_file).open("r",encoding="utf-8") as file:
            pins=json.load(file)
    except (OSError,ValueError,UnicodeError):
        print(json.dumps({"status":"blocked","reason":"invalid_pins_file",
                          "real_inference_verified":False},sort_keys=True))
        return 3
    outcome=probe_local_embedding_model(args.model_dir,pins,real_inference=True)
    print(json.dumps(outcome,sort_keys=True))
    return 0 if outcome["status"]=="ready" and outcome["real_inference_verified"] else 3


if __name__=="__main__":
    raise SystemExit(main())
