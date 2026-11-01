"""Project-scoped authorization of an explicitly selected PUBLIC TikTok list.

No external right/ownership or license is claimed. This is an operator's
documented research-workflow permission, not a blanket right to redistribute,
access private media, or override a provider's ToS. Only exact validated post
IDs in the pinned Bright Data selection can be registered. No model/provider
calls happen here.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from intelligence import config
from intelligence.discovery import service as d1
from intelligence.project_sources.repository import _project,_insert,_lookup_active
from intelligence.project_sources.normalize import normalize_url
from .tiktok_top100 import canonical_tiktok_profile,rank_profile_posts

METHOD="marketing-public-tiktok-research-opt-in/v1"

def read_selection(username,*,root:Path):
    handle,_=canonical_tiktok_profile(username)
    state_file=root/("tiktok-top100-"+handle+"-state.json")
    state=json.loads(state_file.read_text(encoding="utf-8"))
    if (state.get("state")!="completed" or state.get("username")!=handle
        or not isinstance(state.get("selection_sha256"),str)):
        raise ValueError("top100_selection_not_completed")
    selection_file=root/("tiktok-top100-"+handle+"-selection.json")
    raw=selection_file.read_bytes()
    if sha256(raw).hexdigest()!=state["selection_sha256"]:
        raise ValueError("top100_selection_sha256_mismatch")
    selected=json.loads(raw)
    if (selected.get("schema_version")!="marketing-tiktok-top100-snapshot/v1"
        or selected.get("profile_snapshot_id")!=state.get("snapshot_id")
        or selected.get("username")!=handle
        or selected.get("selected")!=100 or selected.get("requested")!=100
        or selected.get("ready") is not True):
        raise ValueError("top100_exact_selection_not_ready")
    posts=selected.get("posts")
    if type(posts) is not list or len(posts)!=100:
        raise ValueError("top100_selection_size_invalid")
    # Revalidate every selection including exact order and numeric views
    # through the pure strict ranking function (no network calls).
    ranked=rank_profile_posts(handle,[
        {"url":p.get("url"),"play_count":p.get("views"),
         "type":"photo" if p.get("content_kind")=="photo_carousel" else "video"}
        for p in posts],limit=100)
    if ranked["posts"]!=posts:
        raise ValueError("top100_selection_ranking_changed")
    return selected,state["selection_sha256"]

def transfer_allowed():
    return (config.setting("CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED","")=="1"
            and config.setting("CM_MARKET_VISION_ENABLED","")=="1")

def register_selection(project_id:str,username:str,*,root:Path):
    selection,selection_sha=read_selection(username,root=root)
    _project(project_id)
    posts=selection["posts"]
    handle,profile=canonical_tiktok_profile(username)
    send=transfer_allowed()
    ids=[]
    # All 100 posts and their local analysis policy are registered as one
    # transaction. No account-level permission silently spreads to other posts.
    with d1.connect() as conn:
        with conn.transaction():
            creator=conn.execute("""
                INSERT INTO creators(platform,platform_creator_id,username,canonical_url)
                VALUES ('tiktok',%s,%s,%s)
                ON CONFLICT(platform,platform_creator_id) DO UPDATE
                SET canonical_url=EXCLUDED.canonical_url
                RETURNING id
            """,(profile,handle,profile)).fetchone()[0]
            for p in posts:
                url=p["url"]
                # Reuse the *oldest* exact post source that the batch's
                # _source_for_video() will read, even if its registry kind
                # is research_link/other. Creating a second approved source
                # beside an older denied source would leave the batch blocked.
                from psycopg.rows import dict_row
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("""
                        SELECT * FROM project_sources
                        WHERE project_id=%s AND platform='tiktok'
                          AND platform_native_id=%s AND link_kind='video'
                          AND source_type IN ('user_video_reference','research_link','other')
                          AND tombstoned_at IS NULL
                        ORDER BY created_at LIMIT 1
                    """,(project_id,p["native_id"]))
                    src=cur.fetchone()
                if src is None:
                    src,_=_insert(conn,project_id,
                                  source_type="user_video_reference",url=url)
                if src is None:
                    parsed=normalize_url(url,"user_video_reference")
                    src=_lookup_active(conn,project_id,parsed.identity_key)
                if (src is None or src["canonical_url"]!=url
                    or src["link_kind"]!="video" or not src["active_version_id"]
                    or src["source_type"] not in ("user_video_reference","research_link","other")):
                    raise ValueError("top100_source_identity_conflict")
                proof={
                    "policy":METHOD,"operator_selected":True,
                    "public_platform":"tiktok","profile_url":profile,
                    "profile_snapshot_id":selection["profile_snapshot_id"],
                    "selection_sha256":selection_sha,
                    "license_from_creator_asserted":False,
                    "private_or_restricted_access_permitted":False,
                    "external_model_transfer_enabled":send,
                }
                conn.execute("""
                    UPDATE project_sources
                    SET can_fetch=TRUE,can_store=TRUE,can_analyze=TRUE,
                        can_send_to_llm=%s,rights_policy=%s::jsonb,
                        updated_at=GREATEST(now(),updated_at+interval '1 microsecond')
                    WHERE project_id=%s AND id=%s AND active_version_id=%s
                      AND tombstoned_at IS NULL AND link_kind='video'
                """,(send,json.dumps(proof),project_id,src["id"],src["active_version_id"]))
                created=conn.execute("""
                    INSERT INTO videos(platform,platform_video_id,canonical_url,creator_id)
                    VALUES('tiktok',%s,%s,%s)
                    ON CONFLICT(platform,platform_video_id) DO NOTHING
                    RETURNING id
                """,(p["native_id"],url,creator)).fetchone()
                if created:
                    video_id=created[0]
                else:
                    found=conn.execute("""
                        SELECT id,canonical_url,creator_id FROM videos
                        WHERE platform='tiktok' AND platform_video_id=%s
                    """,(p["native_id"],)).fetchone()
                    if not found or found[1]!=url or (found[2] is not None and found[2]!=creator):
                        raise ValueError("top100_existing_video_identity_conflict")
                    video_id=found[0]
                    if found[2] is None:
                        conn.execute("UPDATE videos SET creator_id=%s WHERE id=%s",
                                     (creator,video_id))
                ids.append(video_id)
    if len(ids)!=100 or len(set(ids))!=100:
        raise ValueError("top100_registration_did_not_produce_exact_100")
    return {"project_id":project_id,"username":handle,
            "registered_exact_public_posts":len(ids),"video_ids":ids,
            "photo_carousels":sum(p["content_kind"]=="photo_carousel" for p in posts),
            "regular_videos":sum(p["content_kind"]=="video" for p in posts),
            "research_policy":METHOD,"external_model_transfer_enabled":send,
            "claimed_creator_license":False,"provider_requests":0,"billable_requests":0}
