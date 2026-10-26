"""SP-06 operator-only offline CLI for manifest creation/verification.

No Hugging Face/OpenRouter imports; no model download or network transport.
This tool records operator-supplied upstream metadata, it cannot independently
attest that a revision really belongs to that upstream model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .model_manifest import (
    MANIFEST_FILENAME, build_model_manifest, verify_model_manifest,
)


def main(argv=None):
    p = argparse.ArgumentParser(description="Prepare/verify offline SP local embedding model pin")
    p.add_argument("--model-dir", required=True, help="Already provisioned model folder")
    p.add_argument("--write", action="store_true", help="Create a manifest; never overwrite")
    p.add_argument("--model-id", help="Verified upstream owner/model ID")
    p.add_argument("--revision", help="Operator-verified 40-digit git commit SHA")
    p.add_argument("--dimensions", type=int, help="True SentenceTransformer vector size")
    args = p.parse_args(argv)
    manifest_file = Path(args.model_dir).expanduser().absolute() / MANIFEST_FILENAME
    try:
        if args.write:
            if not all((args.model_id, args.revision, args.dimensions)):
                p.error("--write requires --model-id, --revision and --dimensions")
            data = build_model_manifest(
                args.model_dir, model_id=args.model_id,
                revision=args.revision, dimensions=args.dimensions,
            )
            # Deliberately exclusive. Existing pins cannot be silently changed.
            with manifest_file.open("x", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
                f.write("\n")
        elif any((args.model_id, args.revision, args.dimensions)):
            p.error("model metadata arguments are only accepted with --write")
        pin = verify_model_manifest(args.model_dir)
    except (PipelineError, OSError) as exc:
        p.exit(1, "Embedding model provisioning failed: " + str(exc) + "\n")
    print(json.dumps({
        "schema_version": pin["manifest"]["schema_version"],
        "model_id": pin["manifest"]["model_id"],
        "revision": pin["manifest"]["revision"],
        "dimensions": pin["manifest"]["dimensions"],
        "artifact_count": len(pin["manifest"]["files"]),
        "model_key": pin["model_key"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
