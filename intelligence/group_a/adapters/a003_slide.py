"""Read-only, explicit A003 video-slide adapter for GA17.

Never choose "latest" analysis: host must supply an A003 ID for each video
media_asset_id. All 003 fields and segment clocks are checked against the
GA05 source SHA/policy before GA17 may bind them into A004.
"""
from __future__ import annotations

from ..contracts_v1 import canonical_sha256
from .carousel import CarouselError
from .mixed_carousel import A003_BINDING, MAX_SEGMENTS, _bind_video

SEGMENT_TABLES = (
    ("scene", "video_feature_a_scenes"),
    ("speech", "video_feature_a_speech_segments"),
    ("screen_text", "video_feature_a_screen_text"),
    ("audio", "video_feature_a_audio_events"),
)


class PinnedA003SlideReader:
    """Explicit read-only adapter: map video media asset ID -> pinned A003 ID.

    This adapter is a reader, NOT an A003 writer; the GA18 assembly stage
    must create a completed, source-SHA-attributed A003 analysis first.
    """
    def __init__(self, conn, *, analysis_ids):
        if (type(analysis_ids) is not dict
            or any(type(k) is not int or k <= 0 or
                   (v is not None and (type(v) is not int or v <= 0))
                   for k,v in analysis_ids.items())):
            raise CarouselError("a003_analysis_id_map_invalid")
        self.conn = conn
        self.analysis_ids = dict(analysis_ids)

    def __call__(self, manifest, item):
        if item["kind"] != "video":
            raise CarouselError("a003_nonvideo_lookup")
        aid = item["media_asset_id"]
        if aid not in self.analysis_ids:
            raise CarouselError("a003_explicit_selection_missing")
        analysis_id = self.analysis_ids[aid]
        if analysis_id is None:
            return None
        row = self.conn.execute(
            """SELECT va.id,va.video_id,va.media_asset_id,va.source_id,
                      va.source_policy_version_id,va.state,va.duration_sec,
                      va.aspect_ratio,va.audio_status,va.measurement_methods,
                      a.sha256,a.permission,(a.expires_at IS NULL OR a.expires_at>now()),
                      s.status,s.can_analyze_media,sp.is_active,sp.permissions
               FROM video_feature_a_analyses va
               JOIN media_assets a ON a.id=va.media_asset_id
               JOIN sources s ON s.id=va.source_id
               JOIN source_policy_versions sp
                 ON sp.id=va.source_policy_version_id AND sp.source_id=va.source_id
               WHERE va.id=%s FOR SHARE OF va,a,s,sp""",
            (analysis_id,)
        ).fetchone()
        if row is None:
            raise CarouselError("a003_analysis_unavailable")
        (dbid,vid,media_id,src,policy,state,duration,ratio,audio_status,methods,
         asset_sha,permission,unexpired,source_state,can_analyze,active,rights) = row
        if (vid != manifest["video_id"] or media_id != aid
            or src != manifest["source_id"]
            or policy != manifest["source_policy_version_id"]
            or asset_sha != item["sha256"]
            or permission != "analysis_allowed" or unexpired is not True
            or source_state != "enabled" or can_analyze is not True
            or active is not True or not isinstance(rights, dict)
            or rights.get("can_analyze_media") is not True
            or not isinstance(methods, dict)
            or methods.get("ga_asset_sha256") != item["sha256"]):
            raise CarouselError("a003_binding_rights_or_digest_invalid")
        timeline=[]
        for kind, table in SEGMENT_TABLES:
            rows = self.conn.execute(
                "SELECT start_sec,end_sec FROM " + table +
                " WHERE analysis_id=%s ORDER BY start_sec,end_sec LIMIT %s",
                (analysis_id, MAX_SEGMENTS + 1)
            ).fetchall()
            if len(rows) > MAX_SEGMENTS or len(timeline)+len(rows) > MAX_SEGMENTS:
                raise CarouselError("a003_segment_budget_exceeded")
            timeline.extend({
                "kind": kind, "start_sec": format(start,".3f"),
                "end_sec": format(end,".3f")
            } for start,end in rows)
        from decimal import Decimal
        timeline.sort(key=lambda x:(Decimal(x["start_sec"]),Decimal(x["end_sec"]),x["kind"]))
        core={
            "schema_version":A003_BINDING,"analysis_id":dbid,
            "media_asset_id":media_id,"source_media_sha256":asset_sha,
            "video_id":vid,"source_id":src,"source_policy_version_id":policy,
            "state":state,
            "duration_sec":None if duration is None else format(duration,".3f"),
            "aspect_ratio":ratio,"audio_status":audio_status,
            "timeline":timeline,
        }
        binding={**core,"binding_sha256":canonical_sha256(core)}
        return _bind_video(manifest,item,binding)
