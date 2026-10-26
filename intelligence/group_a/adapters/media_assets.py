"""GA-MOD-05: strict public D1/QDP media catalog adapter, opt-in only.

QDP owns qualification; D1 owns asset locations and rights. This adapter
reads (never mutates) their documented SQL tables and verifies actual bytes
through media_manifest. Multi-slide order MUST come from an explicit trusted
ordered-selector provided by the integration host, never from media_assets ID.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Callable

from ..contracts_v1 import qdp_handoff_v1
from ..media_manifest import (
    MediaVerificationError, _roots, build_manifest, open_verified,
)
from ..ports import PEER_VERSIONS, STATE_AVAILABLE, negotiate, unavailable


class VerifiedMediaCatalog:
    REQUIRED_TABLES = ("qdp_preparations","qdp_enrichments","qdp_items",
                       "videos","sources","source_policy_versions","media_assets")

    def __init__(self, conn, *, roots, observed_version=None,
                 ordered_selector=None, clock=None):
        self._conn = conn
        self._roots = roots
        self._version = observed_version
        if ordered_selector is not None and not callable(ordered_selector):
            raise TypeError("ordered_selector must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._selector = ordered_selector
        self._clock = clock
        self._handles = {}

    def capability(self):
        cap = negotiate("media",self._version,"media_contract_not_negotiated")
        if cap.state != STATE_AVAILABLE:
            return cap
        try:
            _roots(self._roots)
            for name in self.REQUIRED_TABLES:
                row=self._conn.execute("SELECT to_regclass(%s)",(name,)).fetchone()
                if row is None or row[0] is None:
                    return unavailable("media","media_public_schema_missing")
        except Exception:
            return unavailable("media","media_catalog_unavailable")
        return cap

    def read_verified_manifest(self, pointer):
        # No stale file handles after a failed rights or byte check.
        self._handles={}
        p=qdp_handoff_v1(pointer)
        if self.capability().state != STATE_AVAILABLE:
            raise MediaVerificationError("media_catalog_unavailable")
        # READ locks keep source/policy and preparation stable for the
        # caller's receipt transaction. No QDP private Python imports.
        row=self._conn.execute(
            """SELECT v.content_kind,e.source_id,e.source_policy_version_id,
                      e.media_asset_id,e.expires_at,s.status,s.can_analyze_media,
                      sp.is_active,sp.permissions,p.state,p.input_sha256,
                      i.status, (e.expires_at IS NULL OR e.expires_at>now())
               FROM qdp_preparations p
               JOIN qdp_items i ON i.run_id=p.run_id AND i.project_id=p.project_id
                 AND i.video_id=p.video_id
               JOIN qdp_enrichments e ON e.run_id=p.run_id AND
                 e.project_id=p.project_id AND e.video_id=p.video_id
               JOIN videos v ON v.id=p.video_id
               JOIN sources s ON s.id=e.source_id
               JOIN source_policy_versions sp ON sp.id=e.source_policy_version_id
                 AND sp.source_id=e.source_id
               WHERE p.run_id=%s::uuid AND p.project_id=%s AND p.video_id=%s
                 AND p.analysis_group='A'
               FOR SHARE OF p,i,e,v,s,sp""",
            (p["qdp_run_id"],p["project_id"],p["video_id"]),
        ).fetchone()
        if row is None:
            raise MediaVerificationError("preparation_not_found")
        (kind,source,policy,selected,enrich_expiry,source_status,can_analyze,
         active,rights,prep_state,prep_hash,item_state,enrich_fresh)=row
        if not (kind in ("video","slideshow_video","photo_carousel",
                         "mixed_carousel","image_post")
                and type(selected) is int and selected>0
                and prep_state=="ready" and prep_hash==p["input_sha256"]
                and item_state in ("ready","partial")
                and source_status=="enabled" and can_analyze is True
                and active is True and type(rights) is dict
                and rights.get("can_analyze_media") is True
                and enrich_fresh is True):
            raise MediaVerificationError("media_rights_or_preparation_revoked")
        if p.get("preferred_media_asset_id") is not None and selected!=p["preferred_media_asset_id"]:
            raise MediaVerificationError("selected_asset_mismatch")
        if kind in ("video","slideshow_video","image_post"):
            ids=[selected]
        else:
            if self._selector is None:
                raise MediaVerificationError("carousel_order_unavailable")
            # Trusted selector returns canonical slide-order asset IDs.
            # No guessed positions, source URL extraction or ID sorting.
            ids=self._selector(self._conn,p,selected,kind)
            if type(ids) is not list or len(ids)<2 or len(ids)>100 or any(
                type(x) is not int or x<1 for x in ids
            ) or len(set(ids))!=len(ids) or selected not in ids:
                raise MediaVerificationError("invalid_ordered_carousel")
        rows=[]
        for asset_id in ids:
            a=self._conn.execute(
                """SELECT id,video_id,source_id,media_kind,local_path,sha256,
                          permission,(expires_at IS NULL OR expires_at>now())
                   FROM media_assets WHERE id=%s FOR SHARE""",
                (asset_id,),
            ).fetchone()
            if a is None:
                raise MediaVerificationError("asset_not_found")
            (aid,video_id,asset_source,media_kind,path,sha,permission,unexpired)=a
            if type(aid) is not int or video_id!=p["video_id"] or asset_source!=source:
                raise MediaVerificationError("foreign_media_asset")
            if permission != "analysis_allowed" or unexpired is not True:
                raise MediaVerificationError("media_rights_or_retention_expired")
            if type(sha) is not str or len(sha)!=64:
                raise MediaVerificationError("asset_sha_missing")
            if media_kind not in ("video","image"):
                raise MediaVerificationError("asset_kind_unverified")
            rows.append({
                "media_asset_id":aid,"video_id":video_id,
                "source_id":asset_source,"media_kind":media_kind,
                "local_path":path,"sha256":sha,
            })
        verified_at=self._clock() if self._clock else datetime.now().astimezone()
        manifest,handles=build_manifest(
            p,source_id=source,source_policy_version_id=policy,
            content_kind=kind,ordered_assets=rows,roots=self._roots,
            verified_at=verified_at,
        )
        # Invalidate old refs before admitting a new manifest. No stale refs
        # survive a subsequent failed or changed D1/bytes verification.
        self._handles=handles
        return manifest

    @contextmanager
    def open_verified_ref(self, ref):
        """Return a verified, already-open file, never raw SQL local_path."""
        if type(ref) is not str or ref not in self._handles:
            raise MediaVerificationError("unknown_verified_ref")
        with open_verified(self._handles[ref],roots=self._roots) as stream:
            yield stream
