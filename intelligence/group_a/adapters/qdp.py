"""Opt-in QDP v1 public database reader: no import of private QDP Python code.

Constructed only with an explicit transaction-bound PostgreSQL connection and
an explicitly negotiated version. Table presence alone is not authorization.
The caller must revalidate current QDP preparation, source policy and media
ownership in the SAME transaction before recording GA inbox/job.
"""
from __future__ import annotations

from ..contracts_v1 import qdp_handoff_v1
from ..ports import PEER_VERSIONS, STATE_AVAILABLE, negotiate, unavailable


class PostgresQDPReader:
    REQUIRED_RELATIONS = ("qdp_preparations", "qdp_items", "qdp_runs",
                          "qdp_enrichments", "media_assets",
                          "sources", "source_policy_versions")

    def __init__(self, conn, *, observed_version: str | None = None):
        self._conn = conn
        self._observed_version = observed_version

    def capability(self):
        if self._observed_version is None:
            return unavailable("qdp", "qdp_contract_not_negotiated")
        claim = negotiate("qdp", self._observed_version)
        if claim.state != STATE_AVAILABLE:
            return claim
        try:
            for name in self.REQUIRED_RELATIONS:
                row = self._conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()
                if not row or row[0] is None:
                    return unavailable("qdp", "qdp_public_schema_missing")
        except Exception:
            return unavailable("qdp", "qdp_probe_failed")
        return claim

    def read_preparation(self, pointer):
        p = qdp_handoff_v1(pointer)
        cap = self.capability()
        if cap.state != STATE_AVAILABLE:
            raise RuntimeError("QDP handoff unavailable: " + cap.state)
        # READ locks serialize with revocations, without importing qdp.repository.
        # QDP can still change after ACK; consumers must revalidate at execute.
        row = self._conn.execute(
            """SELECT p.state,p.input_sha256,p.inputs,i.status,r.status,
                      e.source_id,e.source_policy_version_id,e.media_asset_id,
                      e.expires_at,m.video_id,m.source_id,m.sha256,m.permission,
                      m.expires_at,s.status,s.can_analyze_media,
                      sp.is_active,sp.permissions
               FROM qdp_preparations p
               JOIN qdp_items i ON i.run_id=p.run_id AND i.project_id=p.project_id
                 AND i.video_id=p.video_id
               JOIN qdp_runs r ON r.id=p.run_id AND r.project_id=p.project_id
               JOIN qdp_enrichments e ON e.run_id=p.run_id
                 AND e.project_id=p.project_id AND e.video_id=p.video_id
               JOIN media_assets m ON m.id=e.media_asset_id
               JOIN sources s ON s.id=e.source_id
               JOIN source_policy_versions sp ON sp.id=e.source_policy_version_id
                 AND sp.source_id=e.source_id
               WHERE p.run_id=%s::uuid AND p.project_id=%s AND p.video_id=%s
                 AND p.analysis_group='A'
               FOR SHARE OF p,i,r,e,m,s,sp""",
            (p["qdp_run_id"],p["project_id"],p["video_id"]),
        ).fetchone()
        if row is None:
            return None
        (state, stored_sha, inputs, item_status, run_status,
         source_id, policy_id, media_id, enrich_expiry, asset_video,
         asset_source, asset_sha, permission, asset_expiry,
         source_status, can_analyze, policy_active, policy_permissions) = row
        valid = (
            state == "ready" and stored_sha == p["input_sha256"]
            and item_status in ("ready","partial")
            and run_status in ("partial","completed")
            and isinstance(inputs,dict) and inputs.get("media_asset_id")==media_id
            and type(media_id) is int and media_id > 0
            and asset_video==p["video_id"] and asset_source==source_id
            and isinstance(asset_sha,str) and len(asset_sha)==64
            and permission not in (None,"metadata_only")
            and source_status=="enabled" and can_analyze is True
            and policy_active is True and isinstance(policy_permissions,dict)
            and policy_permissions.get("can_analyze_media") is True
        )
        if p.get("preferred_media_asset_id") is not None:
            valid = valid and p["preferred_media_asset_id"]==media_id
        # expiry expressions are evaluated by PostgreSQL (no timezone races).
        if valid:
            still_current = self._conn.execute(
                """SELECT ( (%s::timestamptz IS NULL OR %s::timestamptz>now())
                     AND (%s::timestamptz IS NULL OR %s::timestamptz>now()) )""",
                (enrich_expiry,enrich_expiry,asset_expiry,asset_expiry),
            ).fetchone()[0]
            valid = still_current is True
        return {"pointer": dict(p), "state":"ready" if valid else "blocked"}


class CallbackQDPReader:
    """Adapter for peer-owned public QDP reader API supplied by integration host.

    A callback is not a proof: it must return current authoritative preparation
    in the documented shape. The host owns authentication and project scoping.
    """

    def __init__(self, read_current, *, observed_version=None):
        if not callable(read_current):
            raise TypeError("read_current must be callable")
        self._read = read_current
        self._version = observed_version

    def capability(self):
        return negotiate("qdp", self._version, "qdp_reader_not_negotiated")

    def read_preparation(self, pointer):
        if self.capability().state != STATE_AVAILABLE:
            raise RuntimeError("QDP peer version not negotiated")
        return self._read(qdp_handoff_v1(pointer))
