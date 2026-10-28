"""M37 source-pinned, read-only Collector/QDP compatibility audit.

Operate on an *explicit local mirror* of exact peer branch files. SHA1 below
are Git blob identities actually read from their named branch commits.
No imports from QDP or Collector. No network/API/SQL/paid provider operations.
A source path missing or SHA mismatch is BLOCKED, never "compatible by guess".
"""
from __future__ import annotations

import hashlib
from pathlib import Path

VERSION="sre-peer-contract-audit/v1"
REPO="BanSins7/ContentMachine"
PEERS={
    "collector":{
        "branch":"feature/video-collector-v1",
        "commit":"8e17f32aeb2501ec4dc0f6aca892e360201a6e8d",
        "files":{
            "intelligence/collector/sre_delivery.py":
                "8ffe77bfd70a179d5e466c7771118eaf2a757a74",
            "intelligence/collector/ports.py":
                "03016bf36e5e52349f0a971562cb50eb06ae206b",
        },
    },
    "qdp":{
        "branch":"feature/qualified-data-pipeline-v1-20261023",
        "commit":"a0bb24e1c2d6ebd7be0a69c57a1978b66747cbfa",
        "files":{
            "intelligence/qualified_data/sre_inbox.py":
                "97bbdee780cb4a602d7ec0a432e901970daca320",
            "intelligence/qualified_data/intake.py":
                "2b551ed8ab16ebdce8973e09ebb57815c32b32c8",
            "intelligence/qualified_data/repository.py":
                "4d6bbe4b3183f8fea157a28b1f62aa779ac3142a",
        },
    },
}
ISSUES={
    "collector":{
        "code":"COLLECTOR_ADAPTER_NOT_INSTALLED",
        "owner":"Collector deployment host + SRE",
        "status":"requires_operator_install_and_pg17",
        "scenario":"Collector opt-in dispatcher defaults disabled and requires injected capabilities/deliver port with persisted sre.inbox_ack/v1; M35 inbox response is not this ACK format",
        "adapter":"SRE collector_inbox_adapter.CollectorDurableInboxAdapter, disabled by default; host must inject authoritative project->approved publication resolver and actor/DB connection",
    },
    "qdp":{
        "code":"QDP_EVENT_AND_STORAGE_MISMATCH",
        "owner":"QDP",
        "status":"source_contract_incompatible",
        "scenario":"QDP FIX-05 receiver expects strict run-level sre-analytics-event/v1 and sre.classification_run_completed while M36 emits per-video sre.classifications_ready/v1; FIX-01 source rereads sre_classification_runs/results and sre_human_labels, not M11/M30/M32 LIVE result tables",
        "adapter":"QDP-owned versioned run/item adapter + authoritative sre_live_runs, sre_live_items, sre_live_verified_pair_results, sre_live_human_reviews and current D1/C9 read gate; reject offline/legacy, no fake legacy mirror rows",
    },
}

# Deliberately narrow evidence checks: verified exact source hash is primary
# authority, markers document expected contract semantics, not their quality.
MARKERS={
    "intelligence/collector/sre_delivery.py":(
        'SRE_INBOUND_VERSION="sre.discovery_ready/v1"',
        'ACK_VERSION="sre.inbox_ack/v1"',
        "class DurableSREInboxPort(Protocol):",
        "receipt=adapter.deliver(dict(event))",
        "_validate_ack(receipt,event)",
        'require_uuid(candidate,"collector_query_id")',
    ),
    "intelligence/collector/ports.py":(
        'SRE_READY_EVENT = "collector.sre_ready/v1"',
        "MAX_D1_ID = (2 ** 63) - 1",
        "def _query_uuid(",
    ),
    "intelligence/qualified_data/sre_inbox.py":(
        'VERSION = "sre-analytics-event/v1"',
        'COMPLETED = "sre.classification_run_completed"',
        "def validate_completion_event(",
        "_sre_snapshot(",
    ),
    "intelligence/qualified_data/intake.py":(
        "FROM sre_classification_runs r",
        "FROM sre_classification_results result",
        "FROM sre_human_labels h",
        "def require_sre_live_schema(",
    ),
    "intelligence/qualified_data/repository.py":(
        "def _sre_snapshot(",
        "read_live_sre_run",
        "verify_live_coverage",
    ),
}


def git_blob_id(content):
    if type(content) is not bytes:
        raise TypeError("Git blob content requires exact bytes")
    return hashlib.sha1(
        b"blob "+str(len(content)).encode("ascii")+b"\x00"+content
    ).hexdigest()


def inspect_peer_mirror(root):
    """Zero side effects; explicit root/collector and root/qdp checkouts.

    Caller can obtain exact pinned commits via GitHub; no auto-clone, no
    adjacent private project contents, no writes or network calls.
    """
    base=Path(root)
    results=[]
    for peer,meta in PEERS.items():
        for path,digest in meta["files"].items():
            target=base/peer/path
            row={
                "peer":peer,"commit":meta["commit"],"branch":meta["branch"],
                "path":path,"expected_git_blob_sha1":digest,
                "actual_git_blob_sha1":None,"state":"missing",
                "failed_markers":[],
            }
            try:
                if not target.is_file() or target.is_symlink():
                    results.append(row)
                    continue
                raw=target.read_bytes()
                # Guard accidental large/untrusted mirror input without
                # declaring it a source-compatible project checkout.
                if len(raw)>1024*1024:
                    row["state"]="oversize"
                    results.append(row)
                    continue
                row["actual_git_blob_sha1"]=git_blob_id(raw)
                if row["actual_git_blob_sha1"]!=digest:
                    row["state"]="sha_mismatch"
                    results.append(row)
                    continue
                source=raw.decode("utf-8")
                row["failed_markers"]=[
                    marker for marker in MARKERS[path] if marker not in source
                ]
                row["state"]="verified" if not row["failed_markers"] else "contract_marker_missing"
            except (OSError,UnicodeError,ValueError):
                row["state"]="unreadable"
            results.append(row)
    peer_states={
        peer:("source_verified"
              if all(r["state"]=="verified" for r in results if r["peer"]==peer)
              else "blocked_missing_or_changed_source")
        for peer in PEERS
    }
    return {
        "schema_version":VERSION,
        "repo":REPO,
        "peer_pins":{peer:{
            "branch":meta["branch"],"commit":meta["commit"],
        } for peer,meta in PEERS.items()},
        "files":results,
        "peer_source_states":peer_states,
        "findings":[{"peer":p,**issue} for p,issue in ISSUES.items()],
        "collector_event_syntax_overlap": "sre.discovery_ready/v1",
        "collector_uuid_query_to_d1_bigint_cast_allowed":False,
        "sre_m36_event_contract":"sre.classifications_ready/v1",
        "qdp_receiver_event_contract":"sre-analytics-event/v1",
        "qdp_reread_uses_sre_m30_m32_tables":False,
        "qdp_owner_patch_needed":True,
        "ecosystem_connected":"blocked",
        "real_e2e_executed":False,
        "paid_calls":0,
    }


def report_without_mirror():
    """Pinned-source research report, NOT a positive verification claim."""
    return {
        "schema_version":VERSION,
        "peer_pins":{peer:{
            "branch":meta["branch"],"commit":meta["commit"],
            "git_blob_pins":dict(meta["files"]),
        } for peer,meta in PEERS.items()},
        "findings":[{"peer":p,**issue} for p,issue in ISSUES.items()],
        "collector_uuid_query_to_d1_bigint_cast_allowed":False,
        "ecosystem_connected":"blocked",
        "local_mirror_verified":False,"real_e2e_executed":False,
        "paid_calls":0,
    }


__all__=["VERSION","PEERS","MARKERS","git_blob_id",
         "inspect_peer_mirror","report_without_mirror"]
