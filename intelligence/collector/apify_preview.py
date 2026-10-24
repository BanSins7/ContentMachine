"""Strict *offline* Clockworks/Apify item conversion, without ingestion.

A validated candidate is NOT permission to store it. Only separate provider
policy authorization can allow writing metrics or video/media into Discovery.
"""
from __future__ import annotations
from intelligence.discovery.normalize import validate_candidate

def inspect_tiktok_item(item):
    """Return reviewable normalized metadata, never media or hidden personal data.

    The public Actor output schema contains optional webVideoUrl, id, text,
    createTimeISO, playCount and isSlideshow. Only three basic metadata
    fields are included here; play/like/save counts are NOT admitted because
    D1 manual-source rights prohibit storing provider metrics.
    """
    if not isinstance(item,dict):
        return {"state":"rejected","reason":"not_an_object"}
    if item.get("error") or item.get("errorCode"):
        return {"state":"rejected","reason":"provider_reported_error"}
    if item.get("isSlideshow") is True:
        return {"state":"rejected","reason":"slideshow_not_video"}
    url=item.get("webVideoUrl")
    if not isinstance(url,str) or not url.strip():
        return {"state":"rejected","reason":"missing_public_video_url"}
    title=item.get("text")
    if title is not None and (not isinstance(title,str) or len(title)>2000):
        return {"state":"rejected","reason":"invalid_caption"}
    raw={"url":url}
    if title:
        raw["title"]=title[:500]
    published=item.get("createTimeISO")
    if published is not None:
        if not isinstance(published,str):
            return {"state":"rejected","reason":"invalid_publication_date"}
        raw["published_at"]=published
    checked=validate_candidate(raw)
    if checked.state=="rejected" or checked.candidate is None:
        return {"state":"rejected","reason":"invalid_video_metadata",
                "validation_reasons":list(checked.reasons)}
    candidate=checked.candidate
    if "/photo/" in candidate.canonical_url:
        return {"state":"rejected","reason":"photo_post_not_video"}
    if candidate.platform!="tiktok":
        return {"state":"rejected","reason":"wrong_platform"}
    listed_id=item.get("id")
    if listed_id is not None and str(listed_id)!=candidate.platform_video_id:
        return {"state":"rejected","reason":"provider_video_id_mismatch"}
    return {"state":checked.state,"platform":"tiktok",
            "platform_video_id":candidate.platform_video_id,
            "canonical_url":candidate.canonical_url,
            "title":candidate.title,
            "published_at":candidate.published_at.isoformat() if candidate.published_at else None,
            "metrics_imported":False,"media_imported":False,
            "source":"apify/clockworks~tiktok-scraper",
            "note":"Inspection only; requires provider permissions and authenticated ingestion before persistence."}

def inspect_items(items,max_items=50):
    if type(max_items) is not int or not 1<=max_items<=100:
        raise ValueError("Inspection max_items must be 1-100")
    if not isinstance(items,list) or len(items)>max_items:
        raise ValueError("Actor response exceeds explicitly allowed inspection size")
    inspected=[inspect_tiktok_item(item) for item in items]
    duplicates=set()
    seen=set()
    for index,result in enumerate(inspected):
        key=result.get("platform_video_id")
        if key and key in seen:duplicates.add(index)
        if key:seen.add(key)
    return {"inspected":len(inspected),
            "accepted":sum(i["state"]!="rejected" for i in inspected)-len(duplicates),
            "rejected":sum(i["state"]=="rejected" for i in inspected),
            "duplicate_rows":sorted(duplicates),
            "items":[({**i,"state":"duplicate","reason":"canonical_duplicate"} if j in duplicates else i)
                     for j,i in enumerate(inspected)],
            "persisted":0,"paid_calls":0}
