"""Group A pure orchestration readiness, isolated from I/O and paid providers.

Planning validates a QDP pointer and a current, authoritative peer snapshot
through injected ports; this stage NEVER creates tasks or writes A003/A004.
Durable queue/inbox/ACK and live start are reserved for GA-MOD-03/04.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .adapters import disconnected_ports
from .config import GASettings
from .contracts_v1 import (ContractViolation, ensure_same_lineage,
                           media_manifest_v1, qdp_handoff_v1)
from .ports import (GAPorts, PeerCapability, REQUIRED_EXECUTION_PORTS,
                    STATE_AVAILABLE, unavailable)

SCHEMA = "group-a-plan/v1"


@dataclass(frozen=True, slots=True)
class Plan:
    state: str
    reason_codes: tuple[str, ...]
    project_id: str | None
    qdp_run_id: str | None
    video_id: int | None
    manifest_sha256: str | None

    def wire(self) -> dict:
        return {
            "schema_version": SCHEMA, "state": self.state,
            "reason_codes": list(self.reason_codes),
            "project_id": self.project_id, "qdp_run_id": self.qdp_run_id,
            "video_id": self.video_id, "manifest_sha256": self.manifest_sha256,
        }


class NotReady(RuntimeError):
    def __init__(self, codes: tuple[str, ...]):
        self.code = "group_a_not_ready"
        self.reason_codes = codes
        super().__init__("Group A has no live QDP integration: " + ",".join(codes))


def _capability(adapters: GAPorts, port: str) -> PeerCapability:
    """Hostile adapter payloads cannot grant capabilities to a different port."""
    adapter = getattr(adapters, port)
    try:
        value = adapter.capability()
    except (AttributeError, RuntimeError, ValueError):
        return unavailable(port, "capability_probe_failed")
    if not isinstance(value, PeerCapability) or value.port != port:
        return unavailable(port, "invalid_capability")
    return value


def readiness(adapters: GAPorts, settings: GASettings) -> tuple[str, ...]:
    """Returns stable deterministic refusal codes, no dynamic imports."""
    reasons = []
    if not settings.enabled:
        reasons.append("module_disabled")
    if not settings.qdp_handoff_enabled:
        reasons.append("qdp_handoff_disabled")
    for port in REQUIRED_EXECUTION_PORTS:
        value = _capability(adapters, port)
        if value.state != STATE_AVAILABLE:
            reasons.append(port + "_" + value.state)
    # No wired transaction boundary in GA-MOD-02 even if injected fakes look
    # complete. A true live activation also needs the versioned GA SQL/inbox.
    reasons.append("durable_handoff_not_installed")
    return tuple(reasons)


class GroupAService:
    def __init__(self, ports: GAPorts | None = None,
                 settings: GASettings | None = None):
        self.ports = disconnected_ports() if ports is None else ports
        self.settings = GASettings() if settings is None else settings
        if not isinstance(self.ports, GAPorts) or not isinstance(self.settings, GASettings):
            raise TypeError("Explicit Group A ports and settings required")

    def capabilities(self) -> dict:
        observed = {
            key: _capability(self.ports, key).wire()
            for key in ("qdp", "media", "transcript", "vision", "results", "handoff", "queue")
        }
        codes = readiness(self.ports, self.settings)
        return {
            "schema_version": "group-a-port-readiness/v1",
            "state": "unavailable" if codes else "available",
            "reason_codes": list(codes),
            "peers": observed,
            "flags": {
                "enabled": self.settings.enabled,
                "qdp_handoff_enabled": self.settings.qdp_handoff_enabled,
                "allow_psi_reuse": self.settings.allow_psi_reuse,
            },
        }

    def inspect(self, pointer: Mapping[str, Any]) -> Plan:
        """Dry-run only; always no durable writes, no background workers."""
        p = qdp_handoff_v1(pointer)
        reasons = readiness(self.ports, self.settings)
        if reasons:
            return Plan("unavailable", reasons, p["project_id"], p["qdp_run_id"],
                        p["video_id"], None)
        # This branch is currently unreachable until future GA stage explicitly
        # replaces the durable gate. Keep non-mutating peer validation central.
        return self.validate_authoritative_pointer(p)

    def validate_authoritative_pointer(self, pointer: Mapping[str, Any]) -> Plan:
        """Pure planning against injected readers; never accepts caller's event alone.

        Callable for adapter contract/unit tests; NOT an activation mechanism.
        """
        p = qdp_handoff_v1(pointer)
        qcap, mcap = _capability(self.ports, "qdp"), _capability(self.ports, "media")
        if qcap.state != STATE_AVAILABLE or mcap.state != STATE_AVAILABLE:
            missing = tuple(x + "_unavailable" for x, c in (("qdp", qcap), ("media", mcap))
                            if c.state != STATE_AVAILABLE)
            return Plan("unavailable", missing, p["project_id"],
                        p["qdp_run_id"], p["video_id"], None)
        try:
            current = self.ports.qdp.read_preparation(p)
        except (OSError, RuntimeError, TimeoutError, ConnectionError):
            return Plan("unavailable", ("qdp_read_failed",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if current is None:
            return Plan("blocked", ("preparation_missing",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if type(current) is not dict or not {"pointer", "state"}.issubset(current):
            raise ContractViolation("invalid_authoritative_preparation", "qdp.read_preparation")
        live = qdp_handoff_v1(current["pointer"])
        if live != p or current["state"] != "ready":
            return Plan("blocked", ("preparation_changed_or_revoked",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        try:
            raw_manifest = self.ports.media.read_verified_manifest(p)
        except (OSError, RuntimeError, TimeoutError, ConnectionError):
            return Plan("unavailable", ("media_read_failed",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if raw_manifest is None:
            return Plan("blocked", ("verified_media_missing",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        manifest = media_manifest_v1(raw_manifest)
        ensure_same_lineage(p, manifest)
        return Plan("plan_only", ("durable_handoff_not_installed",),
                    p["project_id"], p["qdp_run_id"], p["video_id"],
                    manifest["manifest_sha256"])

    def start(self, pointer: Mapping[str, Any]) -> None:
        """No fake jobs: a durable GA-MOD-04 inbox MUST replace this gate."""
        qdp_handoff_v1(pointer)
        raise NotReady(readiness(self.ports, self.settings))

    def make_qdp_receiver(self, connect, qdp_factory, media_factory):
        """Explicitly inject an opt-in durable QDP intake. No auto-dispatch.

        A stored inbox/queue receipt does not enable analysis workers or imply
        media bytes have been verified by the future GA-MOD-05 resolver.
        """
        from .handoff import GroupAInboxReceiver
        return GroupAInboxReceiver(connect, qdp_factory, media_factory,
                                  settings=self.settings)

    def make_verified_media_catalog(self, conn, roots, *,
                                   ordered_selector=None, clock=None):
        """Explicit GA-MOD-05 D1 catalog, never wired automatically.

        The host MUST inject a same-transaction connection and authorized
        local roots. Carousel positions require a trusted ordered selector.
        No QDP handoff starts solely because this catalog was constructed.
        """
        from .adapters.media_assets import VerifiedMediaCatalog
        return VerifiedMediaCatalog(conn,roots=roots,
                                    observed_version="ga-media-manifest/v1",
                                    ordered_selector=ordered_selector,clock=clock)

    def inspect_local_video(self, path, config=None, *, roots=None,
                            runner=None, ffprobe=None, ffmpeg=None):
        """Authorized offline A1 technical measurement; never publishes QDP/B.

        This is deliberately not a worker or a job intake. An explicit root
        allowlist is mandatory, even when all module flags are disabled.
        Method/version evidence is returned separately from A003 facts.
        """
        from pathlib import Path
        from .extractors.technical import analyze_technical
        import subprocess
        return analyze_technical(Path(path),config,
                                 roots=roots,runner=subprocess.run if runner is None else runner,
                                 ffprobe=ffprobe,ffmpeg=ffmpeg,
                                 strict=True,include_versions=True)

    def sample_local_frames(self, verified_media, probe, *, roots,
                            scenes=(), config=None, store=None,
                            project_id=None, run_id=None,
                            runner=None, ffmpeg=None):
        """Offline-only sparse PNG observations; no SQL result/worker dispatch.

        Callers must provide GA-MOD-05 VerifiedFile + GA-MOD-06 ProbeInfo.
        With storage requested, project/run identity must be explicit; all
        bytes are saved locally under an immutable content-hash namespace.
        """
        from .extractors.frames import sample_frames
        from .extractors.frame_store import FrameStore
        import subprocess
        manifest,blobs=sample_frames(verified_media,probe,roots=roots,
            scenes=scenes,config=config,
            runner=subprocess.run if runner is None else runner,
            ffmpeg=ffmpeg)
        if store is None:
            return manifest,blobs
        if not isinstance(store,FrameStore):
            raise TypeError("FrameStore required")
        reference=store.commit(project_id,run_id,manifest,blobs)
        return manifest,reference

    def reuse_psi(self, binding, *, transcript_reader=None, vision_reader=None):
        """Explicit, rights-checked PSI reuse; never launches ASR/OCR/Sonnet.

        The caller supplies a verified, project-specific PSIAssetBinding and
        current read-only readers sharing its *exact* binding. No PSI lookup
        by Discovery video_id, native URL, or coincidental source UUID.
        When not enabled or absent, return an unavailable decision and leave
        later local pipelines free to perform their own authorized work.
        """
        from .adapters.psi_reuse import PSIAssetBinding, PostgresPSIReader
        from .ports import STATE_AVAILABLE
        if not isinstance(binding,PSIAssetBinding):
            raise TypeError("Explicit PSIAssetBinding required")
        outputs={"schema_version":"ga-psi-reuse-decision/v1",
                 "state":"unavailable","reason_code":"psi_reuse_disabled",
                 "source_media_sha256":binding.media_sha256,
                 "transcript":None,"vision":None}
        if not self.settings.allow_psi_reuse:
            return outputs
        for name,reader in (("transcript",transcript_reader),("vision",vision_reader)):
            if reader is None:
                continue
            if not isinstance(reader,PostgresPSIReader) or reader.binding!=binding or reader.kind!=name:
                raise TypeError("Authoritative PSI reader binding mismatch")
            if reader.capability().state!=STATE_AVAILABLE:
                continue
            current=reader.read_verified(binding.media_sha256)
            if current is not None:
                outputs[name]=current
        if outputs["transcript"] is not None or outputs["vision"] is not None:
            outputs["state"]="partial"
            outputs["reason_code"]="reuse_available_for_observed_streams"
        else:
            outputs["reason_code"]="psi_verified_outputs_missing"
        return outputs

    def inspect_local_audio(self, verified_media, *, roots, config=None,
                            model=None, runner=None, ffprobe=None, ffmpeg=None,
                            audio_stream_index=None):
        """Offline-only explicit GA09 decode + optional Silero candidate VAD.

        Returns (DecodedAudio, VAD wire). DecodedAudio.pcm stays in memory
        and must never be serialized to SQL, logs, HTTP or the Group B wire.
        The model is a preinstalled SHA-pinned local Silero JIT or a separate
        explicit test adapter. Missing weights => VAD 'unavailable', never
        a guessed absence of speech. No automatic worker or API route.
        """
        import subprocess
        from .extractors.audio_decode import decode_audio
        from .extractors.vad import observe_vad
        source=decode_audio(verified_media,roots=roots,
            runner=subprocess.run if runner is None else runner,
            ffprobe=ffprobe,ffmpeg=ffmpeg,
            audio_stream_index=audio_stream_index)
        return source,observe_vad(source,model=model,config=config)

    def transcribe_local_audio(self, decoded_audio, *, config=None,
                               psi=None, psi_verify_current=None,
                               sidecar=None, backend=None, vad=None,
                               video_duration_sec=None):
        """GA-MOD-10 explicit ASR source selection without runtime dispatch.

        PSI requires an authoritative current-rights callable. SRT/TXT and
        PSI cues use independently proven VIDEO duration, not audio duration.
        The backend is an optional offline resource-leased faster-whisper.
        No model/provider weights are downloaded and unmeasured words stay
        unknown. The canonical ASR output does not contain PCM or raw files.
        """
        from .extractors.asr import transcribe
        return transcribe(decoded_audio,config=config,psi=psi,
            psi_verify_current=psi_verify_current,sidecar=sidecar,
            backend=backend,vad=vad,video_duration_sec=video_duration_sec)

    def measure_local_speech(self, decoded_audio, transcript, *,
                             video_probe=None, vad=None, config=None,
                             psi_timeline=None):
        """GA-MOD-11 partial speech facts, never direct A003 publication.

        Callers must bind video_probe to the SAME authoritative verified
        media as decoded_audio. PSI clock alignment requires an authenticated
        acquisition-specific PSITimelineBinding. Output includes an isolated
        A003 speech projection; it does not mark Group A analysis complete.
        """
        from .extractors.speech_metrics import measure_speech
        return measure_speech(decoded_audio,transcript,video_probe=video_probe,
            vad=vad,config=config,psi_timeline=psi_timeline)

    def inspect_local_frame_text(self, frame_manifest, png_blobs, *,
                                 project_id, run_id, backend=None,
                                 config=None, cache=None, verify_current=None):
        """Offline GA-MOD-12 frame OCR, never a completed text-coverage claim.

        The caller must obtain GA07 byte-verified frame evidence from an
        authorized project/run. OCRCache reuse requires the host's current
        source/project/asset rights checker on every lookup. Optional local
        Tesseract models must be preinstalled and SHA-pinned; no download,
        automatic worker, provider, SQL writing or GA13 interval inference.
        """
        from .extractors.ocr import analyze_frame_ocr
        return analyze_frame_ocr(frame_manifest,png_blobs,project_id=project_id,
            run_id=run_id,backend=backend,config=config,cache=cache,
            verify_current=verify_current)

    def track_local_screen_text(self, ocr_observation, frame_manifest, *,
                                config=None, verify_current=None):
        """GA13 sparse OCR recurrence only, never a continuous subtitle claim.

        Mandatory current-authoritative project/run/media rights recheck,
        frozen GA12 OCR and GA07 manifest digests and exact frame identity.
        No direct A003 writer; GA18 requires independent boundary proof.
        """
        from .extractors.text_tracking import track_screen_text
        return track_screen_text(ocr_observation,frame_manifest,
            config=config,verify_current=verify_current)
