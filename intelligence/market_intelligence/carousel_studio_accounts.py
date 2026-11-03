"""Durable, cumulative TikTok profile research over existing Studio runs.

No provider or model calls. Previously completed analyses are reused verbatim.
The current account snapshot is ONE provider observation, not a union of
different timestamps. Individual run artifacts are never overwritten.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlparse

from intelligence.market_intelligence import carousel_studio_analytics as cohort

ACCOUNT_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOCK = threading.RLock()
SCHEMA = "studio-account-master/v1"


def _studio():
    # Lazy import: Studio invokes refresh_master() from its existing packager.
    from intelligence.market_intelligence import carousel_studio
    return carousel_studio


def identity(profile_url: str) -> str:
    """Stable case-insensitive account ID; not a user-controlled path."""
    if type(profile_url) is not str:
        raise ValueError("studio_account_invalid_profile")
    parsed = urlparse(profile_url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.tiktok.com", "tiktok.com"):
        raise ValueError("studio_account_invalid_profile")
    if not re.fullmatch(r"/@[A-Za-z0-9._]{1,32}/?", parsed.path):
        raise ValueError("studio_account_invalid_profile")
    return sha256(profile_url.rstrip("/").casefold().encode("utf-8")).hexdigest()[:32]


def _profile_runs(profile_url=None):
    studio = _studio()
    with studio.db() as conn:
        if profile_url is None:
            rows=conn.execute("""SELECT id,source_input,state,created_at,updated_at,
                completed,failed,total,skipped_existing,provider,model
                FROM studio_runs WHERE source_kind='profile'
                ORDER BY created_at ASC,id ASC""").fetchall()
        else:
            rows=conn.execute("""SELECT id,source_input,state,created_at,updated_at,
                completed,failed,total,skipped_existing,provider,model
                FROM studio_runs WHERE source_kind='profile'
                AND lower(rtrim(source_input,'/'))=lower(rtrim(?,'/'))
                ORDER BY created_at ASC,id ASC""",(profile_url,)).fetchall()
    return [dict(r) for r in rows]


def _existing_profiles():
    """Includes old accounts created before the master feature was installed."""
    urls={}
    for row in _profile_runs():
        url=row["source_input"]
        ident=identity(url)
        urls.setdefault(ident,url)
    return urls


def _folder(account_id):
    if type(account_id) is not str or not ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("studio_account_invalid_id")
    return _studio()._dir()/"accounts"/account_id


def refresh_master(profile_url):
    """Recalculate one cumulative JSON, without triggering any paid requests."""
    studio = _studio()
    aid=identity(profile_url)
    with _LOCK:
        runs=_profile_runs(profile_url)
        if not runs:
            return None
        run_ids=[x["id"] for x in runs]
        # No user-provided SQL identifiers; only bound parameters.
        placeholders=",".join("?" for _ in run_ids)
        with studio.db() as conn:
            item_rows=conn.execute(f"""SELECT i.*,r.created_at AS run_created_at,
                r.provider AS run_provider,r.model AS run_model
                FROM studio_items i JOIN studio_runs r ON r.id=i.run_id
                WHERE i.run_id IN ({placeholders})
                ORDER BY r.created_at ASC,r.id ASC,i.rowid ASC""",run_ids).fetchall()
            snapshot_history=[]
            channel=[]
            # Preserve every provider observation separately, but compare
            # groups against one coherent latest non-empty account snapshot.
            for run in runs:
                rows=conn.execute("""SELECT native_id,kind,canonical_url,counts_json
                    FROM studio_channel_posts WHERE run_id=?
                    ORDER BY native_id""",(run["id"],)).fetchall()
                if not rows:
                    continue
                snapshot_row=conn.execute(
                    "SELECT snapshot_id FROM studio_runs WHERE id=?",
                    (run["id"],)).fetchone()
                snapshot_history.append({
                    "run_id":run["id"],
                    "created_at":run["created_at"],
                    "snapshot_id":snapshot_row["snapshot_id"] if snapshot_row else None,
                    "post_count":len(rows),
                    "posts":[{
                        "native_id":x["native_id"],"kind":x["kind"],
                        "url":x["canonical_url"],
                        "observed_counters":json.loads(x["counts_json"]).get("observed_counters"),
                    } for x in rows],
                })
                channel=[dict(x) for x in rows]

        selected={}
        for row in item_rows:
            item=dict(row)
            native=item["native_id"]
            old=selected.get(native)
            # Never replace a completed source by a failed retry. Prefer the
            # first completed analysis for reproducible provenance.
            if old is None or (old["state"]!="completed" and item["state"]=="completed"):
                selected[native]=item
            elif old["state"]!="completed" and item["state"]!="completed":
                # For incomplete items, prefer the row with verified assets;
                # otherwise prefer the newest attempt.
                if item["manifest_json"] or not old["manifest_json"]:
                    selected[native]=item

        items=list(selected.values())
        groups=cohort.build(items,channel,source_kind="profile")
        exported=[]
        for item in items:
            run_id=item["run_id"]
            native=item["native_id"]
            base=studio._dir()/run_id/"carousels"/native
            def parse(text):
                return json.loads(text) if text else None
            # Include the saved per-post LLM receipt and full source artifact,
            # not just the normalized narrative cached in SQLite.
            receipt_path=base/"analysis.json"
            full_analysis=None
            if receipt_path.is_file() and not receipt_path.is_symlink():
                full_analysis=json.loads(receipt_path.read_text("utf-8"))
            exported.append({
                "native_id":native,
                "url":item["canonical_url"],
                "state":item["state"],
                "source_run_id":run_id,
                "source_run_created_at":item["run_created_at"],
                "provider":item["run_provider"],
                "model":item["run_model"],
                "asset_count":item["asset_count"],
                "error":item["error"],
                "metrics":parse(item["metrics_json"]),
                "gemini":parse(item["analysis_json"]),
                "llm_analysis_artifact":full_analysis,
                "manifest":parse(item["manifest_json"]),
                "artifact_paths_relative_to_studio_root":{
                    name:(f"{run_id}/carousels/{native}/{name}.json"
                          if (base/(name+".json")).is_file() else None)
                    for name in ("analysis","metrics","manifest")},
            })
        completed=sum(x["state"]=="completed" for x in items)
        name="@"+urlparse(profile_url).path.split("@",1)[-1].strip("/")
        run_entries=[{
            "id":r["id"],"created_at":r["created_at"],
            "updated_at":r["updated_at"],"state":r["state"],
            "completed_in_run":r["completed"],"failed_in_run":r["failed"],
            "skipped_existing_in_run":r["skipped_existing"],
            "provider":r["provider"],"model":r["model"],
        } for r in runs]
        latest=snapshot_history[-1] if snapshot_history else None
        master={
            "schema_version":SCHEMA,
            "account":{
                "id":aid,"username":name,
                "profile_url":profile_url,
            },
            "generated_at_utc":studio.utc(),
            "cumulative":{
                "run_count":len(runs),
                "unique_attempted_carousels":len(items),
                "completed_carousels":completed,
                "incomplete_carousels":len(items)-completed,
                "source_run_ids":run_ids,
            },
            "runs":run_entries,
            "latest_profile_snapshot":latest,
            "profile_snapshot_history":snapshot_history,
            "group_analytics":groups,
            "items":exported,
            "interpretation_notes":[
                "One record per TikTok native ID across local profile runs.",
                "A successfully completed analysis is never replaced with a failed later attempt.",
                "Channel comparison uses exactly one most recent non-empty saved profile snapshot.",
                "Older profile snapshots are preserved as separate dated observations, never added to latest-view totals.",
                "Historical post-detail metrics are kept with their original run; they are not treated as current observations.",
                "Saved original LLM receipts, including usage when available, are embedded per item; images remain referenced by local paths.",
                "Creative labels from different LLM models/providers may differ even when the output schema is identical.",
                "The profile snapshot may not contain every publication ever posted.",
                "This JSON is regenerated from local SQLite and files without API or LLM calls.",
            ],
        }
        folder=_folder(aid)
        studio._write_json(folder/"account-master-analysis.json",master)
        studio._write_json(folder/"account-group-analytics.json",groups)
        return master


def _resolve(account_id):
    if type(account_id) is not str or not ACCOUNT_ID.fullmatch(account_id):
        return None
    return _existing_profiles().get(account_id)


def master(account_id):
    """Read the materialized master, rebuilding old missing reports locally."""
    profile=_resolve(account_id)
    if not profile:
        return None
    target=_folder(account_id)/"account-master-analysis.json"
    if not target.is_file() or target.is_symlink():
        return refresh_master(profile)
    try:
        return json.loads(target.read_text("utf-8"))
    except (OSError,ValueError):
        return refresh_master(profile)


def catalog():
    """List known TikTok profiles (not individual runs) for the account picker."""
    profiles=_existing_profiles()
    result=[]
    for account_id,profile in profiles.items():
        report=master(account_id)
        if report is None:
            continue
        counts=report["cumulative"]
        latest=report["runs"][-1]
        result.append({
            "id":account_id,
            "username":report["account"]["username"],
            "profile_url":profile,
            "run_count":counts["run_count"],
            "completed_carousels":counts["completed_carousels"],
            "unique_attempted_carousels":counts["unique_attempted_carousels"],
            "latest_run_id":latest["id"],
            "latest_run_state":latest["state"],
            "latest_run_created_at":latest["created_at"],
            "latest_snapshot_posts":(
                report["latest_profile_snapshot"]["post_count"]
                if report["latest_profile_snapshot"] else None),
        })
    result.sort(key=lambda x:(x["latest_run_created_at"],x["id"]),reverse=True)
    return result


def file_path(account_id,kind):
    names={"json":"account-master-analysis.json",
           "groups":"account-group-analytics.json"}
    if kind not in names:
        return None
    if master(account_id) is None:
        return None
    path=_folder(account_id)/names[kind]
    return path if path.is_file() and not path.is_symlink() else None
