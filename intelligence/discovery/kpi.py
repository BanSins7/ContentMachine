"""D1 manual-only monthly KPI, excluded synthetic/unlabeled/expired data.

The real multi-source eligibility calculation is introduced with collectors D2+.
"""
from __future__ import annotations
from datetime import datetime, timezone
from calendar import monthrange

def manual_monthly_kpi(conn, year=None, month=None):
    now=datetime.now(timezone.utc)
    year=year or now.year
    month=month or now.month
    if not isinstance(year,int) or not 2000<=year<=2100 or not isinstance(month,int) or not 1<=month<=12:
        raise ValueError("Invalid calendar month")
    start=datetime(year,month,1,tzinfo=timezone.utc)
    end=datetime(year+1,1,1,tzinfo=timezone.utc) if month==12 else datetime(year,month+1,1,tzinfo=timezone.utc)
    # All numbers are manual user-owned metadata, not API-derived per-platform ratings.
    discovered=conn.execute("""SELECT count(DISTINCT v.id)
        FROM videos v JOIN discoveries d ON d.video_id=v.id
        WHERE d.source_id='manual_local_d1' AND v.first_seen_at >= %s AND v.first_seen_at < %s""",
        (start,end)).fetchone()[0]
    qualified=conn.execute("""SELECT count(DISTINCT v.id)
        FROM videos v
        JOIN discoveries d ON d.video_id=v.id AND d.source_id='manual_local_d1'
        JOIN source_video_metadata m ON m.video_id=v.id
             AND m.source_id='manual_local_d1'
             AND m.deleted_at IS NULL AND (m.expires_at IS NULL OR m.expires_at > now())
        JOIN video_topics vt ON vt.video_id=v.id
             AND vt.relevance_state='relevant' AND vt.label_origin='human'
        WHERE v.first_seen_at >= %s AND v.first_seen_at < %s
             AND m.published_at >= %s AND m.published_at < %s""",
        (start,end,start,end)).fetchone()[0]
    return {"year":year,"month":month,"monthly_target":10000,
            "discovered":discovered,"manual_labeled_fresh":qualified,
            "qualified_fresh":0,
            "note":"Ручные даты и метки не являются проверкой платформы. Внешнее подтверждение появится с D2/D3."}
