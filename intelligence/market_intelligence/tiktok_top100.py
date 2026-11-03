"""Grounded TikTok top-100 selection from a real profile-posts snapshot.

One username; exact author identity; real provider play counts; photo/video
paths kept distinct; never fill a short list with invented publications.
Ranks only within returned snapshot, not a claim about unseen profile posts.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit
from intelligence.discovery.normalize import canonical_video_url, CandidateError

HANDLE=re.compile(r"[A-Za-z0-9._]{2,30}\Z")
MAX_POSTS=5000
COUNT_MAX=2**63-1

def canonical_tiktok_profile(username:str)->tuple[str,str]:
    if type(username) is not str:
        raise ValueError("tiktok_username_invalid")
    handle=username.strip()
    if handle.startswith("https://"):
        split=urlsplit(handle)
        if (split.hostname not in ("www.tiktok.com","tiktok.com")
            or split.username or split.password or split.port
            or split.query or split.fragment):
            raise ValueError("tiktok_profile_url_invalid")
        handle=split.path.strip("/")
    handle=handle.removeprefix("@")
    if not HANDLE.fullmatch(handle) or handle.startswith(".") or handle.endswith("."):
        raise ValueError("tiktok_username_invalid")
    return handle.lower(),"https://www.tiktok.com/@"+handle.lower()

def _counter(raw):
    if type(raw) is int and 0<=raw<COUNT_MAX:
        return raw
    if type(raw) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,18})",raw):
        n=int(raw)
        if n<COUNT_MAX:return n
    return None

def _kind(raw, path_kind):
    signs=[]
    for key in ("content_type","post_type","type"):
        val=raw.get(key)
        if type(val) is str:
            if val.lower() in ("photo","photos","image","images","slideshow","carousel"):
                signs.append("photo")
            elif val.lower() in ("video","clip"):
                signs.append("video")
    if raw.get("is_photo") is True:
        signs.append("photo")
    if raw.get("is_video") is True:
        signs.append("video")
    if len(set(signs))>1:
        return None
    return signs[0] if signs else path_kind

def rank_profile_posts(username:str, records:list[dict],limit:int=100)->dict:
    handle,profile=canonical_tiktok_profile(username)
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("top_limit_invalid")
    if type(records) is not list or len(records)>MAX_POSTS:
        raise ValueError("profile_snapshot_invalid")
    seen={}
    rejected={"bad_identity":0,"different_author":0,"no_views":0,
              "conflicting_kind":0,"duplicate":0}
    for raw in records:
        if type(raw) is not dict or raw.get("error") or raw.get("error_code"):
            rejected["bad_identity"]+=1;continue
        url=next((raw[k] for k in ("url","post_url","video_url","link")
                  if type(raw.get(k)) is str),None)
        try:
            platform,native,canonical=canonical_video_url(url)
        except (CandidateError,ValueError,TypeError):
            rejected["bad_identity"]+=1;continue
        if platform!="tiktok":
            rejected["bad_identity"]+=1;continue
        parts=urlsplit(canonical).path.split("/")
        if (len(parts)<4 or parts[1].casefold()!="@"+handle
            or parts[2] not in ("photo","video")):
            rejected["different_author"]+=1;continue
        # Provider author metadata is additional corroboration if present.
        author=raw.get("author_username") or raw.get("username")
        if type(author) is dict:
            author=author.get("unique_id") or author.get("username")
        if type(author) is str and author.lstrip("@").casefold()!=handle:
            rejected["different_author"]+=1;continue
        views=next((_counter(raw[k]) for k in
                    ("play_count","view_count","views","video_play_count")
                    if _counter(raw.get(k)) is not None),None)
        if views is None:
            rejected["no_views"]+=1;continue
        kind=_kind(raw,parts[2])
        if kind is None:
            rejected["conflicting_kind"]+=1;continue
        canonical=f"https://www.tiktok.com/@{handle}/{kind}/{native}"
        post={"platform":"tiktok","native_id":native,"url":canonical,
              "content_kind":"photo_carousel" if kind=="photo" else "video",
              "views":views}
        if native in seen:
            rejected["duplicate"]+=1
            # Duplicate source rows with a different kind or count cannot
            # establish a unique ranking observation.
            if seen[native]!=post:
                seen[native]=None
            continue
        seen[native]=post
    valid=[p for p in seen.values() if p is not None]
    valid.sort(key=lambda x:(-x["views"],-int(x["native_id"])))
    top=valid[:limit]
    return {"schema_version":"marketing-tiktok-top100-snapshot/v1",
            "username":handle,"profile_url":profile,"ranking_metric":"public_play_count",
            "ranking_scope":"only_provider_returned_profile_records",
            "provider_records":len(records),"unique_ranked_posts":len(valid),
            "selected":len(top),"requested":limit,"ready":len(top)==limit,
            "incomplete_profile_history_possible":True,
            "rejected":rejected,"posts":top}
