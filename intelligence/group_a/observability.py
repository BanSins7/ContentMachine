"""GA-MOD-21: project- and live-QDP-scoped bounded operator read model.

No provider/media/file access, no path data, no raw transcripts stored in
HTTP response. CPU/GPU numbers represent reserved slot-seconds, not actual
hardware usage or a monetary invoice.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from decimal import Decimal
import json
import re

from .contracts_v1 import canonical_sha256

PROJECT=re.compile(r"^[a-f0-9]{32}$")
UUID=re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA=re.compile(r"^[a-f0-9]{64}$")


class ObservationDenied(RuntimeError):
    def __init__(self,code,status=400):
        self.code=code
        self.status=status
        super().__init__(code)


def safe_project(project):
    if type(project) is not str or PROJECT.fullmatch(project) is None:
        raise ObservationDenied("invalid_project_id")
    return project


def safe_run(run_id):
    if type(run_id) is not str or UUID.fullmatch(run_id) is None:
        raise ObservationDenied("invalid_run_id")
    return run_id


def number(value):
    return None if value is None else float(value)


def iso(value):
    return value.isoformat() if value is not None else None


def safe_limit(value,max_limit=25):
    if type(value) is not int or not 1<=value<=max_limit:
        raise ObservationDenied("invalid_page_limit")
    return value


def encode_cursor(parts):
    raw=json.dumps(parts,separators=(",",":"),ensure_ascii=True).encode("ascii")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw,kind):
    if raw is None:
        return None
    if type(raw) is not str or not 1<=len(raw)<=160 or not re.fullmatch(
        r"[A-Za-z0-9_-]+",raw
    ):
        raise ObservationDenied("invalid_page_cursor")
    try:
        data=json.loads(urlsafe_b64decode(raw+"="*(-len(raw)%4)))
    except (ValueError,UnicodeDecodeError,TypeError) as exc:
        raise ObservationDenied("invalid_page_cursor") from exc
    if kind=="run":
        if (type(data) is not list or len(data)!=2 or
            type(data[0]) is not str or type(data[1]) is not str or
            UUID.fullmatch(data[1]) is None):
            raise ObservationDenied("invalid_page_cursor")
        try:
            dt=datetime.fromisoformat(data[0])
        except ValueError as exc:
            raise ObservationDenied("invalid_page_cursor") from exc
        if dt.tzinfo is None:
            raise ObservationDenied("invalid_page_cursor")
        return (dt,data[1])
    if kind=="event" and (type(data) is list and len(data)==1 and
                          type(data[0]) is int and data[0]>0):
        return tuple(data)
    raise ObservationDenied("invalid_page_cursor")


class GroupAMonitor:
    """Read-only safe projector for a trusted host connection and QDP rights."""
    def __init__(self,connect,verify_current,verify_result):
        if (not callable(connect) or not callable(verify_current)
            or not callable(verify_result)):
            raise ObservationDenied("monitor_ports_missing",503)
        self.connect=connect
        self.verify_current=verify_current
        self.verify_result=verify_result

    def _run(self,conn,project,run_id):
        row=conn.execute(
            """SELECT id::text,project_id,qdp_run_id::text,video_id,source_id,
                      source_policy_version_id,qdp_input_sha256,manifest_sha256,
                      content_kind,state,expected_media_items,attempts,
                      max_attempts,created_at,updated_at,finished_at,
                      invalidated_at
               FROM ga_runs WHERE id=%s::uuid AND project_id=%s
               FOR SHARE""",
            (safe_run(run_id),safe_project(project))
        ).fetchone()
        if row is None:
            raise ObservationDenied("run_not_found",404)
        self._fresh(row)
        return row

    def _fresh(self,row):
        scope={
            "run_id":row[0],"project_id":row[1],"qdp_run_id":row[2],
            "video_id":row[3],"source_id":row[4],
            "source_policy_version_id":row[5],
            "qdp_input_sha256":row[6],"manifest_sha256":row[7],
        }
        if self.verify_current(dict(scope)) is not True:
            raise ObservationDenied("run_not_current_or_not_authorized",404)

    @staticmethod
    def _brief(row):
        return {
            "run_id":row[0],"video_id":row[3],"content_kind":row[8],
            "state":row[9],"expected_media_items":row[10],
            "attempts":row[11],"max_attempts":row[12],
            "created_at":iso(row[13]),"updated_at":iso(row[14]),
            "finished_at":iso(row[15]),"invalidated":row[16] is not None,
        }

    def runs(self,project,*,limit=20,cursor=None):
        safe_project(project)
        safe_limit(limit)
        after=decode_cursor(cursor,"run")
        with self.connect() as conn:
            rows=conn.execute(
                """SELECT id::text,project_id,qdp_run_id::text,video_id,source_id,
                          source_policy_version_id,qdp_input_sha256,manifest_sha256,
                          content_kind,state,expected_media_items,attempts,
                          max_attempts,created_at,updated_at,finished_at,
                          invalidated_at
                   FROM ga_runs
                   WHERE project_id=%s AND (
                       %s::timestamptz IS NULL OR
                       (created_at,id)<(%s::timestamptz,%s::uuid))
                   ORDER BY created_at DESC,id DESC LIMIT %s""",
                (project,iso(after[0]) if after else None,
                 iso(after[0]) if after else None,
                 after[1] if after else None,limit+1)
            ).fetchall()
            # Do not surface a run if external current-QDP or project rights
            # are unavailable. No right to inspect arbitrary old QDP results.
            approved=[]
            for row in rows:
                try:
                    self._fresh(row)
                except ObservationDenied:
                    continue
                approved.append(row)
                if len(approved)>=limit+1:
                    break
            visible=approved[:limit]
            next_cursor=encode_cursor([iso(visible[-1][13]),visible[-1][0]]) if len(approved)>limit else None
            return {"schema_version":"ga21-runs/v1",
                    "items":[self._brief(r) for r in visible],
                    "next_cursor":next_cursor}

    def overview(self,project,run_id):
        with self.connect() as conn:
            r=self._run(conn,project,run_id)
            summary=conn.execute(
                """SELECT state,count(*) FROM ga_steps
                   WHERE run_id=%s::uuid GROUP BY state""",(run_id,)
            ).fetchall()
            outbox=conn.execute(
                """SELECT state,count(*) FROM ga_outbox
                   WHERE run_id=%s::uuid GROUP BY state""",(run_id,)
            ).fetchall()
            return {"schema_version":"ga21-run-overview/v1",
                    "run":self._brief(r),
                    "steps":{str(k):int(n) for k,n in summary},
                    "delivery":{str(k):int(n) for k,n in outbox}}

    def steps(self,project,run_id,*,limit=25,cursor=None):
        safe_limit(limit)
        after=decode_cursor(cursor,"event")
        with self.connect() as conn:
            self._run(conn,project,run_id)
            rows=conn.execute(
                """SELECT s.id,s.step_key,s.step_kind,s.state,s.attempts,
                          s.max_attempts,s.failure_code,
                          s.created_at,s.updated_at,s.finished_at,
                          t.cpu_slots,t.gpu_slots,t.elapsed_attempt_sec,
                          t.reserved_cpu_slot_sec,t.reserved_gpu_slot_sec
                   FROM ga_steps s
                   LEFT JOIN ga21_local_stage_telemetry t ON t.step_id=s.id
                   WHERE s.run_id=%s::uuid
                     AND (%s::bigint IS NULL OR s.id>%s::bigint)
                   ORDER BY s.id ASC LIMIT %s""",
                (run_id,after[0] if after else None,
                 after[0] if after else None,limit+1)
            ).fetchall()
            items=[{
                "step_id":r[0],"step_key":r[1],"kind":r[2],"state":r[3],
                "attempts":r[4],"max_attempts":r[5],
                "failure_code":r[6],"created_at":iso(r[7]),
                "updated_at":iso(r[8]),"finished_at":iso(r[9]),
                "cpu_slots":r[10],"gpu_slots":r[11],
                "elapsed_attempt_sec":number(r[12]),
                "reserved_cpu_slot_sec":number(r[13]),
                "reserved_gpu_slot_sec":number(r[14])
            } for r in rows[:limit]]
            return {"schema_version":"ga21-steps/v1","items":items,
                    "next_cursor":encode_cursor([rows[limit-1][0]]) if len(rows)>limit else None,
                    "telemetry_semantics":"reserved_slots_not_measured_cpu_gpu_usage"}

    def events(self,project,run_id,*,limit=25,cursor=None):
        safe_limit(limit)
        after=decode_cursor(cursor,"event")
        with self.connect() as conn:
            self._run(conn,project,run_id)
            rows=conn.execute(
                """SELECT id,event_kind,step_id,created_at
                   FROM ga21_operator_events
                   WHERE run_id=%s::uuid AND project_id=%s
                     AND (%s::bigint IS NULL OR id<%s::bigint)
                   ORDER BY id DESC LIMIT %s""",
                (run_id,project,after[0] if after else None,
                 after[0] if after else None,limit+1)
            ).fetchall()
            return {"schema_version":"ga21-events/v1",
                    "items":[{"event_id":r[0],"kind":r[1],
                              "step_id":r[2],"created_at":iso(r[3])}
                             for r in rows[:limit]],
                    "next_cursor":encode_cursor([rows[limit-1][0]]) if len(rows)>limit else None}

    def measurements(self,project,run_id,*,limit=25):
        """Only a *locally* completed A result; host live rights still required.

        Raw transcript, screen text, source paths and PCM never cross this API.
        GA20's local view prevents expired or permission-revoked media pins.
        """
        safe_limit(limit)
        with self.connect() as conn:
            self._run(conn,project,run_id)
            row=conn.execute(
                """SELECT result_id,result_sha256,video_a_analysis_id,
                          carousel_a_analysis_id,snapshot
                   FROM ga20_locally_current_results
                   WHERE run_id=%s::uuid AND project_id=%s
                   ORDER BY result_id DESC LIMIT 1""",
                (run_id,project)
            ).fetchone()
            if row is None:
                return {"schema_version":"ga21-measurements/v1","available":False,
                        "reason_code":"no_locally_completed_snapshot"}
            # Local view guards current asset and policy; the external
            # authority must additionally verify the immutable result's
            # source bytes, origin and current QDP before showing measurements.
            if canonical_sha256(row[4])!=row[1] or (
                self.verify_result({
                    "project_id":project,"run_id":run_id,
                    "ga_result_id":row[0],"result_sha256":row[1],
                    "snapshot":dict(row[4]),
                }) is not True
            ):
                raise ObservationDenied("result_not_current_or_not_authorized",404)
            aid,cid=row[2],row[3]
            result={"schema_version":"ga21-measurements/v1","available":True,
                    "ga_result_id":row[0],"result_sha256":row[1],
                    "analysis_type":"video" if aid is not None else "carousel"}
            if aid is not None:
                base=conn.execute(
                    """SELECT state,duration_sec,aspect_ratio,scene_count,
                              scene_status,speech_status,screen_text_status,
                              audio_status,spoken_words,speech_wpm,
                              speech_coverage_pct,has_subtitles,
                              first_text_sec,first_speech_sec,cta_start_sec
                       FROM video_feature_a_summary WHERE analysis_id=%s""",(aid,)
                ).fetchone()
                if base is None:
                    raise ObservationDenied("analysis_missing",404)
                columns=("state","duration_sec","aspect_ratio","scene_count",
                         "scene_status","speech_status","screen_text_status",
                         "audio_status","spoken_words","speech_wpm",
                         "speech_coverage_pct","has_subtitles","first_text_sec",
                         "first_speech_sec","cta_start_sec")
                result["summary"]={k:number(v) if isinstance(v,Decimal) else v
                                   for k,v in zip(columns,base)}
                for label,table in (
                    ("scenes","video_feature_a_scenes"),
                    ("speech","video_feature_a_speech_segments"),
                    ("screen_text","video_feature_a_screen_text"),
                    ("audio_events","video_feature_a_audio_events"),
                ):
                    fields=("scene_index,start_sec,end_sec" if label=="scenes"
                            else "start_sec,end_sec,event_type" if label=="audio_events"
                            else "start_sec,end_sec")
                    # Table/field names are fixed trusted literals; never from URL.
                    rows=conn.execute(
                        "SELECT "+fields+" FROM "+table+
                        " WHERE analysis_id=%s ORDER BY start_sec LIMIT %s",
                        (aid,limit)
                    ).fetchall()
                    result[label]=[{
                        **({"scene_index":r[0]} if label=="scenes" else {}),
                        "start_sec":number(r[1] if label=="scenes" else r[0]),
                        "end_sec":number(r[2] if label=="scenes" else r[1]),
                        **({"event_type":r[2]} if label=="audio_events" else {}),
                    } for r in rows]
            else:
                summary=conn.execute(
                    """SELECT state,slide_count,slide_status,text_status,
                              audio_status,has_audio,total_screen_word_count,
                              first_text_slide,cta_slide_index
                       FROM carousel_feature_a_summary WHERE analysis_id=%s""",
                    (cid,)
                ).fetchone()
                if summary is None:
                    raise ObservationDenied("analysis_missing",404)
                names=("state","slide_count","slide_status","text_status",
                       "audio_status","has_audio","total_screen_word_count",
                       "first_text_slide","cta_slide_index")
                result["summary"]={k:v for k,v in zip(names,summary)}
                rows=conn.execute(
                    """SELECT slide_index,item_type,aspect_ratio,duration_sec,
                              text_status,screen_word_count
                       FROM carousel_feature_a_items WHERE analysis_id=%s
                       ORDER BY slide_index LIMIT %s""",(cid,limit)
                ).fetchall()
                result["slides"]=[{
                    "slide_index":r[0],"kind":r[1],"aspect_ratio":r[2],
                    "duration_sec":number(r[3]),"text_status":r[4],
                    "screen_word_count":r[5]
                } for r in rows]
            self._run(conn,project,run_id)
            return result


def append_operator_event(conn,*,project,run_id,event_kind,
                          actor_ref_sha256,step_id=None):
    """No raw username, path, request body, headers or IP in database."""
    safe_project(project)
    safe_run(run_id)
    if type(actor_ref_sha256) is not str or SHA.fullmatch(actor_ref_sha256) is None:
        raise ObservationDenied("untrusted_operator_identity",403)
    if event_kind not in (
        "dag_scheduled","run_paused","run_resumed","run_cancelled",
        "local_step_retried","run_invalidated"
    ):
        raise ObservationDenied("invalid_operator_action")
    if step_id is not None and (type(step_id) is not int or step_id<=0):
        raise ObservationDenied("invalid_operator_step")
    material={"schema_version":"ga21-operator-event/v1","run_id":run_id,
              "project_id":project,"event_kind":event_kind,
              "step_id":step_id,"actor_ref_sha256":actor_ref_sha256}
    digest=canonical_sha256(material)
    conn.execute(
        """INSERT INTO ga21_operator_events
           (run_id,project_id,event_kind,step_id,actor_ref_sha256,event_sha256)
           VALUES (%s::uuid,%s,%s,%s,%s,%s)
           ON CONFLICT(run_id,event_sha256) DO NOTHING""",
        (run_id,project,event_kind,step_id,actor_ref_sha256,digest)
    )
    return digest
