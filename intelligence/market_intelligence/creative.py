"""Standalone MI creative structure extraction.

The codebooks are copied/adapted from Group B B2/B3 classifications; this
implementation DOES NOT import or read any Group B module or table.

Only a verified subtitle sidecar or otherwise authenticated timed transcript
can substantiate story roles. Lightweight lexical mapping is explicitly
heuristic and is never passed off as ground truth or a visual examination.
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

VERSION = "mi-creative-captions-v1"
HOOK_MECHANISMS = ("problem","promise","curiosity","myth_busting","mistake",
                   "surprise","story","challenge","comparison","proof")
STORY_ROLES = ("hook","context","problem","consequence","goal","myth","mistake",
               "cause","explanation","fact","tip","step","demonstration",
               "comparison","example","proof","testimonial","product_intro",
               "product_demo","before_after","result","reveal","summary",
               "offer","cta","other")
VIDEO_FAMILIES = ("talking_head","dialogue_interview","skit","demonstration",
 "product_showcase","screen_recording","pov","reaction","voiceover_montage",
 "animation_motion_graphics","slideshow_video","vlog_bts")
TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})$")
STRIP_TAGS=re.compile(r"<[^>]{0,250}>")
WHITESPACE=re.compile(r"\s+")

def _seconds(value):
    m=TIME.fullmatch(value.strip())
    if not m: raise ValueError("bad_caption_timestamp")
    h,minutes,sec,frac=(int(x) for x in m.groups())
    if minutes>=60 or sec>=60: raise ValueError("bad_caption_timestamp")
    return round(h*3600+minutes*60+sec+frac/1000,3)

def parse_srt(text: str, *, max_seconds: float = 600.0) -> list[dict]:
    """Copy of timestamp/interval guards from A subtitle processing, adapted to MI."""
    if not isinstance(text,str) or len(text.encode("utf-8"))>500_000:
        raise ValueError("caption_file_too_large")
    rows=[]
    for block in re.split(r"\r?\n\s*\r?\n",text.strip()):
        lines=block.strip().splitlines()
        stamps=next((i for i,s in enumerate(lines[:2]) if "-->" in s),None)
        if stamps is None: continue
        pair=lines[stamps].split("-->")
        if len(pair)!=2: continue
        try:
            start=_seconds(pair[0]);end=_seconds(pair[1])
        except ValueError: continue
        if not (0<=start<end<=max_seconds+0.2):continue
        body=WHITESPACE.sub(" ",STRIP_TAGS.sub("", " ".join(lines[stamps+1:]))).strip()
        if not body:continue
        if len(body)>1800:body=body[:1800]
        rows.append({"start_sec":start,"end_sec":end,"text":body})
        if len(rows)>=1500:break
    return sorted(rows,key=lambda x:(x["start_sec"],x["end_sec"]))

def _meaning(text: str, first: bool) -> tuple[str,list[str]]:
    s=text.casefold()
    mech=[]
    role="context"
    if any(x in s for x in ("ошибк","mistake","wrong","не делай","don't do")):
        mech.append("mistake");role="mistake"
    elif any(x in s for x in ("проблем","отек","отёк","puffy","problem","pain","why ")) or s.endswith("?"):
        mech.append("problem");role="problem"
    if any(x in s for x in ("до и после","до/после","before and after","before/after")):
        mech.append("proof");role="before_after"
    elif any(x in s for x in ("сравн","versus"," vs ","compare")):
        mech.append("comparison");role="comparison"
    elif any(x in s for x in ("результат","result","в итоге","finally")):
        mech.append("proof");role="result"
    elif any(x in s for x in ("шаг ","step ","сначала","затем","first ","next ")):
        role="step"
    elif any(x in s for x in ("подпиш","сохран","скачай","download","follow me","install ")):
        role="cta"
    if first:
        if s.endswith("?") or s.startswith(("как ","почему ","how ","why ","what ")):
            mech.append("curiosity")
        role="hook"
    return role,sorted(set(mech))

def analyze_captions(segments: list[dict], *, duration: float | None = None) -> dict:
    if not isinstance(segments,list) or len(segments)>1500:
        raise ValueError("invalid_segments")
    validated=[]
    for row in segments:
        if not isinstance(row,dict) or type(row.get("text")) is not str:
            raise ValueError("invalid_segment")
        try: start=float(row["start_sec"]);end=float(row["end_sec"])
        except (TypeError,ValueError):raise ValueError("invalid_segment_time")
        if not 0<=start<end<=600 or (duration is not None and end>duration+0.3):
            raise ValueError("invalid_segment_interval")
        validated.append({"start_sec":start,"end_sec":end,"text":row["text"][:1800]})
    validated.sort(key=lambda r:r["start_sec"])
    if not validated:
        return {"method":VERSION,"state":"unavailable","format_family":None,
                "hook_mechanisms":[],"hook_text":None,"story_roles":[],
                "subtitle_segments":0,"limitations":["no_verified_timed_transcript"]}
    roles=[];mechanisms=[]
    first=validated[0]
    for i,row in enumerate(validated):
        role,cues=_meaning(row["text"],i==0 and row["start_sec"]<=5)
        if role!="context" and (not roles or roles[-1]!=role): roles.append(role)
        if i==0 and row["start_sec"]<=5: mechanisms.extend(cues)
    if not roles: roles=["context"]
    # A caption-derived family is weak and must not masquerade as Group B
    # video-primary visual classification.
    family="captioned_explainer" if len(validated)>=3 and len(roles)>=2 else None
    return {"method":VERSION,"state":"heuristic_partial","format_family":family,
            "hook_mechanisms":sorted(set(mechanisms)),
            "hook_text": first["text"][:320] if first["start_sec"]<=5 else None,
            "story_roles":roles[:12],
            "subtitle_segments":len(validated),
            "limitations":["lexical_heuristics_only","visual_mechanics_unknown",
                           "not_equivalent_to_full_creative_classification"]}
