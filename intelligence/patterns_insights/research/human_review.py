"""PIE-MOD-32: fail-closed, append-only human review and feedback chain.

A review is NOT an approved/published insight. The host must supply a genuinely
authenticated actor grant; the legacy pie_insight_reviews table is not used.
No freeform reviewer text, private source rows or model output can be released.
"""
from __future__ import annotations

from dataclasses import dataclass,field
from datetime import datetime,timezone
from hashlib import sha256
from typing import Protocol
import json
import re
import uuid

from ..contracts import PIEBoundaryError,canonical_hash
from ..intake.provenance import SourceEligibilityGate
from .pattern_queries import RegistryQuery,CurrentProjectAuthorizer,FreshUAEReader,CursorSigner
from .insight_verification import verify_candidate_insight,CandidateInsightVerification

SCHEMA='pie-human-review-event/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_ACTOR=re.compile(r'[A-Za-z0-9_.:@-]{1,160}\Z')
_MAX_EVENTS=100000
_ROLES={
    'review_decision':'reviewer',
    'change_suggestion':'reviewer',
    'appeal':'appellant',
    'adjudication':'adjudicator',
    'calibration_vote':'reviewer',
    'calibration_gold':'curator',
}
_DECISIONS={
    'review_decision':frozenset(('review_candidate_internal','reject_candidate','request_changes')),
    'adjudication':frozenset(('uphold_rejection','return_to_review')),
}
_REASONS=frozenset((
    'evidence_consistent','insufficient_support','numerical_disagreement',
    'rights_or_scope_dispute','missing_counterevidence',
    'clarify_uncertainty','clarify_scope','correct_source_unit',
    'request_more_evidence','challenge_prior_decision','policy_reassessment',
    'independent_calibration','unresolved_disagreement',
))
_EDITS=frozenset((
    'clarify_uncertainty','clarify_scope','correct_source_unit',
    'add_caveat','retest_hypothesis',
))
_LABELS=frozenset(('candidate_consistent','candidate_contradictory','uncertain'))
_REJECT=frozenset(('reject_candidate','request_changes'))


def _fail(code:str,msg:str)->None:
    raise PIEBoundaryError(code,msg)


def _canon(d:dict)->str:
    return json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _uuid(value)->bool:
    if type(value) is not str:return False
    try:return str(uuid.UUID(value))==value
    except (TypeError,ValueError,AttributeError):return False


@dataclass(frozen=True,slots=True)
class ReviewAction:
    request_id:str
    kind:str
    reason_code:str
    decision_code:str|None=None
    edit_code:str|None=None
    calibration_label:str|None=None
    target_event_sha256:str|None=None

    def __post_init__(self):
        if (not _uuid(self.request_id) or type(self.kind) is not str
            or self.kind not in _ROLES
            or type(self.reason_code) is not str
            or self.reason_code not in _REASONS):
            _fail('invalid_review_action','Exact request UUID, event type and reason required')
        expected=self.kind
        if expected in _DECISIONS:
            if (type(self.decision_code) is not str
                or self.decision_code not in _DECISIONS[expected]):
                _fail('invalid_review_action','Unknown review/adjudication decision')
        elif self.decision_code is not None:
            _fail('invalid_review_action','Decision field must be absent')
        if expected=='change_suggestion':
            if type(self.edit_code) is not str or self.edit_code not in _EDITS:
                _fail('invalid_review_action','Only enumerated non-executing edit proposals')
        elif self.edit_code is not None:
            _fail('invalid_review_action','Edit field must be absent')
        if expected in ('calibration_vote','calibration_gold'):
            if type(self.calibration_label) is not str or self.calibration_label not in _LABELS:
                _fail('invalid_review_action','Unverified typed calibration label required')
        elif self.calibration_label is not None:
            _fail('invalid_review_action','Calibration label must be absent')
        if expected in ('appeal','adjudication'):
            if (type(self.target_event_sha256) is not str
                or not _SHA.fullmatch(self.target_event_sha256)):
                _fail('invalid_review_action','Exact prior event target required')
        elif self.target_event_sha256 is not None:
            _fail('invalid_review_action','Unsolicited linked event target')
        if (expected=='review_decision' and
            ((self.decision_code=='review_candidate_internal' and
              self.reason_code!='evidence_consistent')
             or (self.decision_code in _REJECT and
                 self.reason_code not in (
                    'insufficient_support','numerical_disagreement',
                    'rights_or_scope_dispute','missing_counterevidence')))):
            _fail('invalid_review_action','Evidence-consistent and rejection reasons differ')
        if expected=='change_suggestion' and self.reason_code not in (
                'clarify_uncertainty','clarify_scope','correct_source_unit',
                'request_more_evidence','missing_counterevidence'):
            _fail('invalid_review_action','No arbitrary narrative for reviewer edits')
        if expected=='appeal' and self.reason_code not in (
                'challenge_prior_decision','policy_reassessment'):
            _fail('invalid_review_action','Only bounded appeal reasons')
        if expected=='adjudication' and self.reason_code!='policy_reassessment':
            _fail('invalid_review_action','Independent adjudication is a policy assessment')
        if expected in ('calibration_vote','calibration_gold') and self.reason_code not in (
                'independent_calibration','unresolved_disagreement'):
            _fail('invalid_review_action','Calibrations must use independent label rules')


@dataclass(frozen=True,slots=True)
class CurrentReviewGrant:
    project_id:str
    actor_id:str
    role:str
    grant_sha256:str
    expires_at:datetime
    policy_schema:str='pie-human-review-authorization/v1'
    granted_by_host:bool=True

    def __post_init__(self):
        if (type(self.project_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',self.project_id)
            or type(self.actor_id) is not str or not _ACTOR.fullmatch(self.actor_id)
            or self.role not in set(_ROLES.values())
            or type(self.grant_sha256) is not str or not _SHA.fullmatch(self.grant_sha256)
            or type(self.expires_at) is not datetime
            or self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
            or self.policy_schema!='pie-human-review-authorization/v1'
            or self.granted_by_host is not True):
            _fail('review_authorization_invalid','Host grant must be exact scoped and expiring')


class CurrentReviewAuthority(Protocol):
    """Host-supplied authenticated actor source, NOT an in-project mock."""
    def require_current_review_grant(
        self,project_id:str,actor_id:str,event_kind:str,
        generation_sha256:str)->CurrentReviewGrant:...


@dataclass(frozen=True,slots=True)
class CandidateReviewEvent:
    event_sha256:str
    request_sha256:str
    project_id:str
    generation_sha256:str
    verification_sha256:str
    event_seq:int
    parent_event_sha256:str|None
    actor_id:str
    actor_role:str
    kind:str
    review_edit_version:int
    _json:str=field(repr=False)
    @property
    def may_publish(self)->bool:return False
    @property
    def may_public_read(self)->bool:return False
    def as_dict(self)->dict:return json.loads(self._json)
    def to_json(self)->str:return self._json


@dataclass(frozen=True,slots=True)
class CandidateReviewReceipt:
    project_id:str
    generation_sha256:str
    event_sha256:str
    event_seq:int
    idempotent:bool
    internal_state:str
    reviewer_disagreement:bool
    calibration_consensus:str|None
    may_publish:bool=False
    may_public_read:bool=False
    human_release_approved:bool=False


def _check_grant(authority:CurrentReviewAuthority,project_id:str,
                 actor_id:str,action:ReviewAction,generation:str,
                 now:datetime)->CurrentReviewGrant:
    if (type(actor_id) is not str or not _ACTOR.fullmatch(actor_id)
        or authority is None
        or not callable(getattr(authority,'require_current_review_grant',None))):
        _fail('review_authority_unavailable','Trusted authenticated reviewer authority required')
    try:
        grant=authority.require_current_review_grant(project_id,actor_id,
                                                      action.kind,generation)
    except Exception:
        _fail('review_access_denied','No authenticated current human review role')
    if (type(grant) is not CurrentReviewGrant
        or grant.project_id!=project_id or grant.actor_id!=actor_id
        or grant.role!=_ROLES[action.kind] or grant.expires_at<=now):
        _fail('review_access_denied','Role, scope or expiry not valid for this action')
    return grant


def _rows_from_history(rows:list|tuple,project_id:str,generation:str,
                       verification:str)->list[dict]:
    if type(rows) not in (tuple,list) or len(rows)>_MAX_EVENTS:
        _fail('review_chain_corrupt','Unbounded review history')
    prev=None;out=[];edit_counter=0;seen_requests=set()
    for i,r in enumerate(rows,1):
        if type(r) not in (tuple,list) or len(r)!=14:
            _fail('review_chain_corrupt','Incomplete event lineage')
        (seq,evt_sha,parent,req_sha,actor,role,kind,decision,edit,label,
         target,ver_sha,payload,edit_version)=r
        if (type(seq) is not int or seq!=i or parent!=prev
            or type(evt_sha) is not str or not _SHA.fullmatch(evt_sha)
            or type(req_sha) is not str or not _SHA.fullmatch(req_sha)
            or req_sha in seen_requests or type(payload) is not dict
            or canonical_hash(payload)!=evt_sha
            or ver_sha!=verification
            or payload.get('schema_version')!=SCHEMA
            or payload.get('project_id')!=project_id
            or payload.get('generation_sha256')!=generation
            or payload.get('event_seq')!=seq
            or payload.get('parent_event_sha256')!=parent
            or payload.get('request_sha256')!=req_sha
            or payload.get('actor_id')!=actor or payload.get('actor_role')!=role
            or payload.get('event_kind')!=kind
            or payload.get('decision_code')!=decision
            or payload.get('edit_code')!=edit
            or payload.get('calibration_label')!=label
            or payload.get('target_event_sha256')!=target
            or payload.get('verification_sha256')!=ver_sha
            or payload.get('review_edit_version')!=edit_version
            or payload.get('may_publish') is not False
            or payload.get('may_public_read') is not False):
            _fail('review_chain_corrupt','Review chain hashes, parent or source disagree')
        if kind=='change_suggestion':edit_counter+=1
        if (type(edit_version) is not int or
            edit_version!=(edit_counter if kind=='change_suggestion' else 0)):
            _fail('review_chain_corrupt','Reviewer proposed edit version is inconsistent')
        out.append({'seq':seq,'sha':evt_sha,'parent':parent,'request':req_sha,
                    'actor':actor,'role':role,'kind':kind,'decision':decision,
                    'edit':edit,'label':label,'target':target,'payload':payload})
        seen_requests.add(req_sha)
        prev=evt_sha
    return out


def _transition(action:ReviewAction,actor:str,source_owner:str,
                report:CandidateInsightVerification,history:list[dict])->None:
    role=_ROLES[action.kind]
    if role in ('reviewer','curator','adjudicator') and actor==source_owner:
        _fail('review_self_review_forbidden','Original author cannot independently review')
    if action.kind=='appeal' and actor!=source_owner:
        _fail('review_appeal_denied','Appeal only from original source owner')
    if (action.kind=='review_decision'
        and action.decision_code=='review_candidate_internal'
        and report.verdict!='candidate_checked_for_review'):
        _fail('review_quarantine_unapprovable','Unverified facts cannot be approved')
    target=None
    if action.target_event_sha256 is not None:
        target=next((x for x in history if x['sha']==action.target_event_sha256),None)
        if target is None:
            _fail('review_target_unavailable','Event target not in this generation')
    if action.kind=='appeal':
        if (target['kind']!='review_decision' or target['decision'] not in _REJECT
            or target['actor']==actor or
            any(x['kind']=='appeal' and x['target']==target['sha'] for x in history)):
            _fail('review_appeal_denied','Only one genuine appeal of a denial allowed')
    if action.kind=='adjudication':
        original=next((x for x in history if target and x['sha']==target['target']),None)
        if (target['kind']!='appeal' or original is None
            or actor in (target['actor'],original['actor'])
            or any(x['kind']=='adjudication' and x['target']==target['sha'] for x in history)):
            _fail('review_adjudication_denied','Independent adjudicator and open appeal required')
    if action.kind=='calibration_vote':
        if any(x['kind']=='calibration_gold' for x in history):
            _fail('review_calibration_conflict','Curator consensus freezes gold label inputs')
        if any(x['kind']=='calibration_vote' and x['actor']==actor for x in history):
            _fail('review_calibration_conflict','One immutable blinded label per reviewer')
    if action.kind=='calibration_gold':
        previous=[x for x in history if x['kind']=='calibration_vote']
        votes={x['actor']:x['label'] for x in previous}
        if (len(votes)<2 or actor in votes
            or set(votes.values())!={action.calibration_label}
            or any(x['kind']=='calibration_gold' for x in history)):
            _fail('review_calibration_conflict','Two independent agreeing voters before curator gold label')


def _state(history:list[dict],verdict:str)->tuple[str,bool,str|None]:
    # Latest opinion from each independent reviewer is the current assessment.
    latest={}
    for x in history:
        if x['kind']=='review_decision':latest[x['actor']]=x['decision']
    opinions=set(latest.values())
    disputed=('review_candidate_internal' in opinions and
              bool(opinions & _REJECT))
    gold=next((x['label'] for x in reversed(history)
               if x['kind']=='calibration_gold'),None)
    answered_appeals={x['target'] for x in history if x['kind']=='adjudication'}
    pending_appeals=any(x['kind']=='appeal' and x['sha'] not in answered_appeals
                        for x in history)
    if verdict!='candidate_checked_for_review':
        state='quarantined'
    elif pending_appeals:
        state='appeal_pending'
    elif disputed:
        state='disputed_internal'
    elif history and history[-1]['kind']=='adjudication':
        state=('reopened_for_review'
               if history[-1]['decision']=='return_to_review' else 'rejection_upheld')
    elif 'reject_candidate' in opinions:
        state='rejected_internal'
    elif 'request_changes' in opinions:
        state='changes_requested_internal'
    elif 'review_candidate_internal' in opinions:
        state='candidate_reviewed_internally_only'
    else:
        state='awaiting_internal_review'
    return state,disputed,gold


def _build_event(project_id:str,generation_sha:str,verification_sha:str,
                 seq:int,parent:str|None,grant:CurrentReviewGrant,
                 action:ReviewAction,edit_version:int)->CandidateReviewEvent:
    request_sha=canonical_hash({
        'project_id':project_id,'generation_sha256':generation_sha,
        'request_id':action.request_id})
    data={
        'schema_version':SCHEMA,'project_id':project_id,
        'generation_sha256':generation_sha,'verification_sha256':verification_sha,
        'event_seq':seq,'parent_event_sha256':parent,
        'request_sha256':request_sha,'request_id':action.request_id,
        'actor_id':grant.actor_id,'actor_role':grant.role,
        'grant_sha256':grant.grant_sha256,
        'event_kind':action.kind,'decision_code':action.decision_code,
        'reason_code':action.reason_code,'edit_code':action.edit_code,
        'calibration_label':action.calibration_label,
        'target_event_sha256':action.target_event_sha256,
        'review_edit_version':edit_version,
        'review_policy':'internal_candidate_only',
        'original_insight_modified':False,
        'model_proposal_is_review_decision':False,
        'human_release_approved':False,
        'may_publish':False,'may_public_read':False,
    }
    raw=_canon(data)
    if len(raw.encode('utf-8'))>4096:
        _fail('review_event_too_large','Bounded private event')
    digest=sha256(raw.encode('utf-8')).hexdigest()
    return CandidateReviewEvent(
        digest,request_sha,project_id,generation_sha,verification_sha,
        seq,parent,grant.actor_id,grant.role,action.kind,edit_version,raw)


def replay_candidate_review_event(raw:str|bytes,sha:str,
                                  expected:CandidateReviewEvent)->CandidateReviewEvent:
    if (type(expected) is not CandidateReviewEvent or type(sha) is not str
        or not _SHA.fullmatch(sha) or sha!=expected.event_sha256):
        _fail('review_replay_invalid','Exact original candidate review required')
    try:data=raw if type(raw) is bytes else raw.encode('utf-8')
    except (UnicodeError,AttributeError) as exc:
        raise PIEBoundaryError('review_replay_invalid','Invalid event bytes') from exc
    if (type(data) is not bytes or data!=expected.to_json().encode('utf-8')
        or sha256(data).hexdigest()!=sha):
        _fail('review_replay_mismatch','Review bytes do not match source chain')
    return expected


def append_candidate_review(
        connection,query:RegistryQuery,series_id:str,generation_sha256:str,
        actor_id:str,action:ReviewAction,authority:CurrentReviewAuthority,
        authorizer:CurrentProjectAuthorizer,reader:FreshUAEReader,
        gate:SourceEligibilityGate,signer:CursorSigner,
        *,now:datetime|None=None)->tuple[CandidateReviewEvent,CandidateReviewReceipt]:
    """Current actor AND source rights required on every append and replay.

    The internal verdict never grants public read, final approval or publishing.
    """
    if (type(query) is not RegistryQuery or type(action) is not ReviewAction
        or type(series_id) is not str or not _SHA.fullmatch(series_id)
        or type(generation_sha256) is not str or not _SHA.fullmatch(generation_sha256)):
        _fail('review_request_invalid','Exact scoped owner, request and candidate IDs required')
    now=now if now is not None else datetime.now(timezone.utc)
    if type(now) is not datetime or now.utcoffset() is None:
        _fail('review_request_invalid','Timezone-aware current review clock required')
    grant=_check_grant(authority,query.project_id,actor_id,action,generation_sha256,now)
    if (grant.role in ('reviewer','adjudicator','curator')
        and actor_id==query.principal_id) or (
        grant.role=='appellant' and actor_id!=query.principal_id):
        _fail('review_role_separation','Owner cannot self-review and only owner can appeal')
    try:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ('pie-registry-project:'+query.project_id,))
            report,_=verify_candidate_insight(
                connection,query,series_id,generation_sha256,
                authorizer,reader,gate,signer,now=now)
            # Lock per generation after source verification; the SQL trigger
            # independently takes the same chain lock for direct SQL writers.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ('pie-review:'+query.project_id+':'+generation_sha256,))
            rows=connection.execute(
                """SELECT event_seq,event_sha256,parent_event_sha256,
                          request_sha256,actor_id,actor_role,event_kind,
                          decision_code,edit_code,calibration_label,
                          target_event_sha256,verification_sha256,event_payload,
                          review_edit_version
                     FROM pie_candidate_insight_review_events
                    WHERE project_id=%s AND generation_sha256=%s
                    ORDER BY event_seq ASC""",
                (query.project_id,generation_sha256)).fetchall()
            history=_rows_from_history(rows,query.project_id,
                                       generation_sha256,report.verification_sha256)
            request_sha=canonical_hash({
                'project_id':query.project_id,'generation_sha256':generation_sha256,
                'request_id':action.request_id})
            duplicate=next((x for x in history if x['request']==request_sha),None)
            if duplicate is not None:
                data=duplicate['payload']
                if (data['actor_id']!=grant.actor_id
                    or data['actor_role']!=grant.role
                    or data['event_kind']!=action.kind
                    or data['decision_code']!=action.decision_code
                    or data['reason_code']!=action.reason_code
                    or data['edit_code']!=action.edit_code
                    or data['calibration_label']!=action.calibration_label
                    or data['target_event_sha256']!=action.target_event_sha256):
                    _fail('review_request_reused','Idempotency request reused for a different human action')
                old=CandidateReviewEvent(
                    duplicate['sha'],request_sha,query.project_id,
                    generation_sha256,report.verification_sha256,
                    duplicate['seq'],duplicate['parent'],grant.actor_id,
                    grant.role,action.kind,data['review_edit_version'],
                    _canon(data))
                state,disputed,gold=_state(history,report.verdict)
                return old,CandidateReviewReceipt(
                    query.project_id,generation_sha256,old.event_sha256,old.event_seq,
                    True,state,disputed,gold)
            _transition(action,actor_id,query.principal_id,report,history)
            seq=len(history)+1
            if seq>_MAX_EVENTS:
                _fail('review_chain_full','Bounded number of human events')
            edit_version=(sum(x['kind']=='change_suggestion' for x in history)+1
                          if action.kind=='change_suggestion' else 0)
            event=_build_event(
                query.project_id,generation_sha256,report.verification_sha256,
                seq,history[-1]['sha'] if history else None,grant,action,edit_version)
            from psycopg.types.json import Jsonb
            inserted=connection.execute(
                """INSERT INTO pie_candidate_insight_review_events
                   (project_id,generation_sha256,verification_sha256,event_seq,
                    parent_event_sha256,event_sha256,request_sha256,
                    review_edit_version,actor_id,actor_role,grant_sha256,
                    event_kind,decision_code,reason_code,edit_code,
                    calibration_label,target_event_sha256,event_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING event_sha256""",
                (event.project_id,event.generation_sha256,event.verification_sha256,
                 event.event_seq,event.parent_event_sha256,event.event_sha256,
                 event.request_sha256,event.review_edit_version,event.actor_id,
                 event.actor_role,grant.grant_sha256,action.kind,action.decision_code,
                 action.reason_code,action.edit_code,action.calibration_label,
                 action.target_event_sha256,Jsonb(event.as_dict()))).fetchone()
            if inserted is None or inserted[0]!=event.event_sha256:
                _fail('review_chain_conflict','New human review event was not stored')
            current=[*history,{'seq':seq,'sha':event.event_sha256,'parent':event.parent_event_sha256,
                    'request':request_sha,'actor':actor_id,'role':grant.role,
                    'kind':action.kind,'decision':action.decision_code,'edit':action.edit_code,
                    'label':action.calibration_label,'target':action.target_event_sha256,
                    'payload':event.as_dict()}]
            state,disputed,gold=_state(current,report.verdict)
            return event,CandidateReviewReceipt(
                query.project_id,generation_sha256,event.event_sha256,seq,
                False,state,disputed,gold)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('review_chain_unavailable',
                               'Human review cannot be committed or authorized') from exc
