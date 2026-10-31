"""GA24 host-injected bridge to the EXISTING Group B exact binding API.

Observed peer: feature/group-b-full-module@0ecc6cc02d7823622ff1b331f16a00073935a283
  intelligence.group_b.lineage.bind_exact_group_a(connect, *,
      b_run_id,ga_result_id,expected_sha256)
This code never imports Group B or writes its SQL. The host must supply an
authenticated B draft selector, the real B binding callable, a DB-backed
read of the committed B pin, and an independent receipt verifier. It can
re-ack an already bound exact B run after lost network acknowledgement.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from .contracts_v1 import canonical_sha256


class GroupBBindDenied(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__("GA24->B rejected: "+code)


def _uuid(raw):
    try:
        if type(raw) is not str or str(UUID(raw))!=raw:
            raise ValueError("invalid uuid")
    except (TypeError,ValueError,AttributeError) as exc:
        raise GroupBBindDenied("invalid_peer_run_uuid") from exc
    return raw


def _sha(raw):
    if (type(raw) is not str or len(raw)!=64 or
        any(x not in "0123456789abcdef" for x in raw)):
        raise GroupBBindDenied("invalid_peer_hash")
    return raw


def _event(raw,key):
    if type(raw) is not dict or set(raw)!={
        "schema_version","ga_run_id","ga_result_id","result_sha256",
        "project_id","qdp_run_id","video_id","source_id",
        "source_policy_version_id","manifest_sha256","content_kind",
        "video_a_analysis_id","carousel_a_analysis_id",
    } or raw["schema_version"]!="group_a.result_ready/v1":
        raise GroupBBindDenied("invalid_ga_outbox_schema")
    _uuid(raw["ga_run_id"])
    _uuid(raw["qdp_run_id"])
    if (type(raw["ga_result_id"]) is not int or raw["ga_result_id"]<1
        or type(raw["video_id"]) is not int or raw["video_id"]<1
        or type(raw["source_policy_version_id"]) is not int
        or raw["source_policy_version_id"]<1
        or type(raw["project_id"]) is not str or len(raw["project_id"])!=32
        or any(c not in "0123456789abcdef" for c in raw["project_id"])
        or type(raw["source_id"]) is not str or not raw["source_id"]):
        raise GroupBBindDenied("invalid_ga_outbox_scope")
    _sha(raw["result_sha256"])
    _sha(raw["manifest_sha256"])
    if type(key) is not str or key!=(
        raw["ga_run_id"]+":"+str(raw["ga_result_id"])+":"+
        raw["result_sha256"]
    ):
        raise GroupBBindDenied("idempotency_key_mismatch")
    video=raw["content_kind"] in ("video","slideshow_video")
    carousel=raw["content_kind"] in ("photo_carousel","mixed_carousel")
    if not (video or carousel):
        raise GroupBBindDenied("b_content_kind_not_supported")
    chosen=("video_a_analysis_id" if video else "carousel_a_analysis_id")
    other=("carousel_a_analysis_id" if video else "video_a_analysis_id")
    if (type(raw[chosen]) is not int or raw[chosen]<1 or
        raw[other] is not None):
        raise GroupBBindDenied("invalid_native_a_analysis_pin")
    return raw


def _proof(event,b_run_id,pin):
    if not isinstance(pin,Mapping):
        raise GroupBBindDenied("b_committed_pin_unavailable")
    expected={
        "b_run_id":b_run_id,
        "project_id":event["project_id"],
        "qdp_run_id":event["qdp_run_id"],
        "video_id":event["video_id"],
        "source_id":event["source_id"],
        "result_sha256":event["result_sha256"],
        "manifest_sha256":event["manifest_sha256"],
        "source_policy_version_id":event["source_policy_version_id"],
        "video_a_analysis_id":event["video_a_analysis_id"],
        "carousel_a_analysis_id":event["carousel_a_analysis_id"],
    }
    if any(pin.get(key)!=value for key,value in expected.items()):
        raise GroupBBindDenied("b_committed_pin_mismatch")
    if pin.get("pinned") is not True:
        raise GroupBBindDenied("b_commit_not_proven")
    return dict(expected,commit_state="exact_a_bound")


class GroupBExactBindTransport:
    """Can be passed as GA20 deliver_once transport= only after real B install.

    Real B requires an existing *draft* B run created from QDP's independent
    Group B intake. A host-supplied exact selector MUST return the same B run
    on repeated event delivery; never "latest B" or make a synthetic B run.
    """
    def __init__(self,*,select_b_run,bind_exact_group_a,
                 read_b_committed_pin,verify_pin_authenticity):
        if any(not callable(fn) for fn in (
            select_b_run,bind_exact_group_a,
            read_b_committed_pin,verify_pin_authenticity
        )):
            raise TypeError("Four real Group B host ports required")
        self.select_b_run=select_b_run
        self.bind_exact_group_a=bind_exact_group_a
        self.read_b_committed_pin=read_b_committed_pin
        self.verify_pin_authenticity=verify_pin_authenticity

    def __call__(self,payload,*,idempotency_key):
        event=_event(payload,idempotency_key)
        b_run_id=_uuid(self.select_b_run(dict(event)))
        # Avoid expensive/side-effectful bind on receipt retry: an exact
        # recorded pin is sufficient only after fresh independent DB proof.
        before=self.read_b_committed_pin(b_run_id)
        if before is not None:
            attested=_proof(event,b_run_id,before)
            if self.verify_pin_authenticity(dict(attested)) is not True:
                raise GroupBBindDenied("b_commit_not_authenticated")
        else:
            self.bind_exact_group_a(
                b_run_id=b_run_id,ga_result_id=event["ga_result_id"],
                expected_sha256=event["result_sha256"]
            )
            after=self.read_b_committed_pin(b_run_id)
            attested=_proof(event,b_run_id,after)
            if self.verify_pin_authenticity(dict(attested)) is not True:
                raise GroupBBindDenied("b_commit_not_authenticated")
        return {
            "ga_result_id":event["ga_result_id"],
            "result_sha256":event["result_sha256"],
            "consumer_receipt_sha256":canonical_sha256({
                "schema_version":"ga24-b-committed-exact-pin/v1",
                **attested,
            }),
        }
