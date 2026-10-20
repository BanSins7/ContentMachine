"""OpenRouter read-only model metadata catalog for the owner's 12 candidates.

Never invokes chat/completions and never requires or exposes API keys.
Each refresh is explicit, snapshots immutable; absence is not auto-enabled.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
from urllib.request import Request, urlopen
from .pipeline_contracts import PipelineError, normalize_capability, sha256, canonical

CATALOG_URL="https://openrouter.ai/api/v1/models"
FRESHNESS=timedelta(hours=48)
MAX_CATALOG_BYTES=12_000_000

def fetch_openrouter_models() -> list[dict]:
    req=Request(CATALOG_URL, headers={"Accept":"application/json",
                                      "User-Agent":"ContentMachine-TaxonomyBuilder/0.3"})
    try:
        with urlopen(req,timeout=12) as response:
            length=response.headers.get("Content-Length")
            if length and int(length)>MAX_CATALOG_BYTES:
                raise PipelineError("Model catalog response is too large",502)
            data=response.read(MAX_CATALOG_BYTES+1)
        if len(data)>MAX_CATALOG_BYTES:
            raise PipelineError("Model catalog response is too large",502)
        parsed=json.loads(data.decode("utf-8"))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("OpenRouter catalog unavailable; no models were changed",503) from exc
    if not isinstance(parsed,dict) or not isinstance(parsed.get("data"),list):
        raise PipelineError("OpenRouter catalog response has an unexpected format",502)
    return parsed["data"]

def refresh_model_catalog(conn, fetcher=fetch_openrouter_models) -> dict:
    """Do networking BEFORE entering the transaction (prevents long DB locks)."""
    raw=fetcher()
    if not isinstance(raw,list):
        raise PipelineError("Invalid catalog payload",502)
    candidates={r[0] for r in conn.execute(
        "SELECT model_id FROM tb_model_candidates").fetchall()}
    found={}
    for row in raw:
        if not isinstance(row,dict) or row.get("id") not in candidates:
            continue
        try:
            cap=normalize_capability(row)
            prices=row.get("pricing") or {}
            if not isinstance(prices,dict):
                continue
        except PipelineError:
            continue  # Not verified; do not enable an inconsistent model.
        found[cap["model_id"]]={"capabilities":cap,"pricing":prices}
    if not found:
        raise PipelineError("No requested models were verified in OpenRouter's catalog",502)
    with conn.transaction():
        for model_id,record in found.items():
            digest=sha256(record)
            conn.execute("""INSERT INTO tb_model_snapshots
                (model_id,catalog_sha256,capabilities,pricing)
                VALUES (%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT(model_id,catalog_sha256) DO NOTHING""",
                (model_id,digest,canonical(record["capabilities"]),
                 canonical(record["pricing"])))
        conn.execute("""INSERT INTO tb_catalog_refreshes(models_present)
                       VALUES (%s::jsonb)""",(canonical(sorted(found)),))
    return {"verified":len(found),"total":len(candidates),"checked_models":sorted(found)}

def _catalog_rows(conn):
    return conn.execute("""
      SELECT c.model_id,c.display_name,
             v.id,v.catalog_sha256,v.capabilities,v.pricing,v.verified_at,
             r.checked_at,r.models_present
      FROM tb_model_candidates c
      LEFT JOIN LATERAL (
        SELECT s.* FROM tb_model_snapshots s
        WHERE s.model_id=c.model_id ORDER BY id DESC LIMIT 1
      ) v ON true
      LEFT JOIN LATERAL (
        SELECT checked_at,models_present FROM tb_catalog_refreshes
        ORDER BY id DESC LIMIT 1
      ) r ON true ORDER BY c.model_id
    """).fetchall()

def list_models(conn) -> list[dict]:
    now=datetime.now(timezone.utc)
    output=[]
    for model_id,name,id,digest,cap,pricing,snapshot_at,checked,available in _catalog_rows(conn):
        present=available is not None and model_id in available
        fresh=checked is not None and now-checked <= FRESHNESS
        verified=id is not None and present and fresh
        usable=bool(verified and cap.get("text_output") and cap.get("supports_structured_outputs"))
        output.append({
          "model_id":model_id,"display_name":name,
          "catalog_version_id":id,"catalog_sha256":digest,
          "capabilities":cap if id is not None else None,
          "pricing":pricing if id is not None else None,
          "verified_at":checked.isoformat() if checked is not None else None,
          "status":("unverified" if id is None else
                    "missing" if not present else "stale" if not fresh else
                    "usable" if usable else "unsupported"),
          "selectable":usable,
        })
    return output

def latest_verified(conn, model_ids: set[str]) -> dict[str,dict]:
    """Fail closed if missing/stale/unsupported, preserving exact snapshot id."""
    entries={x["model_id"]:x for x in list_models(conn)}
    verified={}
    for model_id in model_ids:
        entry=entries.get(model_id)
        if entry is None or not entry["selectable"]:
            raise PipelineError("Model is not verified or usable: "+model_id,409)
        verified[model_id]=entry
    return verified
