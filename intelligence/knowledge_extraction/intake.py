"""B Stage02: project-scoped, repeatable-read, strictly read-only A8/B1 intake.

No HTTP, LLM/provider, SQL writes, profile synchronization, background jobs,
file upload or video parsing. Each public read revalidates *the entire*
approved A Stage11 snapshot before resolving a pinned item.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.project_sources.context_service import _snapshot, _assemble
from .contracts import (
    A_VIDEO_SCHEMA, ContractError, VideoAnalysisPin,
    pinned_video_analyses, validate_context_envelope,
)

PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_OUTPUT_TEXT = frozenset(("document-segments/v1", "web-source-extract/v1"))


class IntakeError(ContractError):
    """Stable, non-sensitive failure code for a failed B1 intake."""


def _project(value: str) -> str:
    if not isinstance(value, str) or PROJECT_ID_RE.fullmatch(value) is None:
        raise IntakeError("invalid_project_id")
    return value


def _uuid(value: str, label: str = "invalid_id") -> str:
    if not isinstance(value, str) or UUID_RE.fullmatch(value.lower()) is None:
        raise IntakeError(label)
    from uuid import UUID
    return str(UUID(value))


def _sha(value: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise IntakeError("invalid_sha256")
    return value


def _connect():
    # No projects.get_project or sync_profile_to_db: those are write-capable.
    from intelligence.project_sources.repository import _connection
    return _connection()


def _stored_quote_matches_output(quote: str, output: dict[str, Any]) -> bool:
    """Conservative local consistency only; not semantic entailment or truth."""
    schema = output.get("schema_version")
    if schema == "document-segments/v1":
        return any(
            isinstance(row, dict)
            and isinstance(row.get("text"), str)
            and quote in row["text"]
            for row in output.get("segments", [])
        )
    if schema == "web-source-extract/v1":
        text = output.get("page_text")
        return isinstance(text, str) and quote in text
    # A's transcript, OCR, fusion remain A observations: not MiMo text input.
    return True


class PostgresAResolver:
    """Implements the frozen Stage01 ImmutableAResolver against real A tables.

    All public methods use their own complete REPEATABLE READ READ ONLY snapshot.
    No authorization of any cost or provider processing happens here.
    """

    def __init__(self, connection_factory: Callable[[], Any] | None = None,
                 *, require_current: bool = True, verify_local_files: bool = True):
        if type(require_current) is not bool or type(verify_local_files) is not bool:
            raise ValueError("Resolver options must be booleans")
        self._factory = connection_factory or _connect
        self.require_current = require_current
        self.verify_local_files = verify_local_files

    @contextmanager
    def _read(self, project_id: str, context_id: str):
        pid = _project(project_id)
        cid = _uuid(context_id, "invalid_context_id")
        with self._factory() as conn:
            # First statement in transaction. Do not call A's _pid(): it can
            # synchronize the disk profile into PostgreSQL.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if conn.execute("SELECT id FROM project_registry WHERE id=%s", (pid,)).fetchone() is None:
                raise IntakeError("project_not_found")
            try:
                envelope = _snapshot(conn, pid, cid, include_payload=True)
            except Exception as exc:
                # Do not expose SQL exception strings or source contents.
                if type(exc).__name__ == "ContextProblem" and getattr(exc, "code", None) == "not_found":
                    raise IntakeError("context_not_found") from exc
                raise IntakeError("snapshot_integrity_failed") from exc
            validate_context_envelope(envelope, expected_project_id=pid)
            if self.require_current:
                try:
                    current_sha = canonical_sha(_assemble(conn, pid))
                except Exception as exc:
                    raise IntakeError("current_sources_unavailable") from exc
                if current_sha != envelope["context_sha256"]:
                    raise IntakeError("stale_snapshot")
            self._verify_sources(conn, pid, envelope)
            self._verify_outputs(conn, pid, envelope)
            self._verify_evidence(conn, pid, envelope)
            yield conn, envelope

    def _verify_sources(self, conn, pid: str, e: dict):
        for source in e["context"]["source_manifest"]:
            sid, vid = source["source_id"], source["source_version_id"]
            row = conn.execute(
                """SELECT s.source_type,s.active_version_id,s.tombstoned_at,
                          v.version_no,v.content_sha256,v.byte_size,v.media_type,
                          v.immutable_asset_ref,v.initial_extraction_status
                     FROM project_sources s
                     LEFT JOIN project_source_versions v
                       ON v.project_id=s.project_id AND v.source_id=s.id AND v.id=%s
                    WHERE s.project_id=%s AND s.id=%s""",
                (vid, pid, sid),
            ).fetchone()
            if row is None:
                raise IntakeError("source_missing")
            stype, active, tombstone, number, digest, size, media, asset_ref, initial = row
            if stype != source["source_type"]:
                raise IntakeError("source_type_mismatch")
            if vid is None:
                if source["excluded_reason"] is None:
                    raise IntakeError("source_version_missing")
                continue
            if number != source["version_no"] or digest != source["sha256"] or (
                size != source["byte_size"] or media != source["media_type"]
            ) or initial != source["processing_status"]:
                raise IntakeError("source_version_integrity")
            if self.require_current and str(active) != vid:
                raise IntakeError("stale_source_version")
            if self.require_current and tombstone is not None and source["excluded_reason"] is None:
                raise IntakeError("archived_source")
            if self.verify_local_files:
                self._verify_file(conn, pid, sid, vid, digest, size, asset_ref)

    @staticmethod
    def _verify_file(conn, pid: str, sid: str, vid: str, digest: str | None,
                     size: int | None, asset_ref: str | None):
        # Source must have an actual registered immutable research-file version
        # when referencing the research library. This intentionally does not
        # dereference arbitrary DB paths or URLs.
        row = conn.execute(
            """SELECT f.source_id,v.content_sha256,v.byte_size,v.storage_ref,
                      m.storage_ref,m.content_sha256,m.byte_size
                 FROM project_research_file_versions v
                 JOIN project_research_files f ON f.project_id=v.project_id
                    AND f.id=v.file_id AND f.source_id=%s
                 JOIN project_media_assets m ON m.project_id=v.project_id
                    AND m.id=v.media_asset_id AND m.source_id=f.source_id
                    AND m.source_version_id=v.source_version_id
                WHERE v.project_id=%s AND v.source_version_id=%s""",
            (sid, pid, vid),
        ).fetchone()
        if row is None:
            if isinstance(asset_ref, str) and asset_ref.startswith(("research_library/", "research/")):
                raise IntakeError("research_file_version_missing")
            return
        _, fsha, fsize, ref, media_ref, media_sha, media_size = row
        if digest != fsha or digest != media_sha or size != fsize or size != media_size or (
            ref != media_ref or ref != asset_ref
        ):
            raise IntakeError("research_file_metadata_mismatch")
        try:
            from intelligence.project_sources.file_library import _project_folder, _safe_path
            folder = _project_folder(pid)
            path = _safe_path(folder, ref, must_exist=True)
            # Read in bounded chunks; never load a PDF/DOCX into the model or log.
            h = hashlib.sha256()
            count = 0
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    count += len(block)
                    if count > fsize:
                        raise IntakeError("research_file_integrity")
                    h.update(block)
            if count != fsize or h.hexdigest() != digest:
                raise IntakeError("research_file_integrity")
        except IntakeError:
            raise
        except (OSError, ValueError) as exc:
            raise IntakeError("research_file_unavailable") from exc
        except Exception as exc:
            # A storage root/path validation error must fail closed.
            raise IntakeError("research_file_unavailable") from exc

    @staticmethod
    def _verify_outputs(conn, pid: str, e: dict):
        for output in e["context"]["analysis_refs"]:
            row = conn.execute(
                """SELECT output_type,schema_version,output_sha256,output,
                          source_id,source_version_id
                     FROM source_processing_outputs
                    WHERE project_id=%s AND id=%s AND source_id=%s AND source_version_id=%s""",
                (pid, output["output_id"], output["source_id"], output["source_version_id"]),
            ).fetchone()
            if row is None:
                raise IntakeError("output_missing")
            kind, schema, digest, data, sid, vid = row
            if (kind != output["output_type"] or schema != output["schema_version"]
                or digest != output["output_sha256"] or not isinstance(data, dict)
                or data.get("schema_version") != schema or canonical_sha(data) != digest):
                raise IntakeError("output_integrity")
            if schema == A_VIDEO_SCHEMA:
                if (data.get("project_id") != pid or data.get("source_id") != str(sid)
                    or data.get("video_source_version_id") != str(vid)):
                    raise IntakeError("video_identity_mismatch")
                source = next(s for s in e["context"]["source_manifest"] if s["source_id"] == str(sid))
                if source["sha256"] is not None and data.get("video_sha256") != source["sha256"]:
                    raise IntakeError("video_content_sha_mismatch")

    @staticmethod
    def _verify_evidence(conn, pid: str, e: dict):
        for record in e["context"]["evidence_manifest"]:
            row = conn.execute(
                """SELECT ev.exact_text_or_observation,ev.locator,ev.source_id,
                          ev.source_version_id,ev.processing_output_id,
                          g.input_output_id,g.input_sha256,g.quote_sha256,
                          g.locator_sha256,g.profile_revision,g.profile_hash
                     FROM project_evidence ev
                     JOIN project_evidence_grounding g ON g.project_id=ev.project_id
                         AND g.evidence_id=ev.id
                    WHERE ev.project_id=%s AND ev.id=%s""",
                (pid, record["evidence_id"]),
            ).fetchone()
            if row is None:
                raise IntakeError("evidence_missing")
            quote, locator, sid, vid, oid, input_oid, input_sha, quote_sha, locator_sha, pr, ph = row
            if (str(sid) != record["source_id"] or str(vid) != record["source_version_id"]
                or (str(oid) if oid else None) != record["processing_output_id"]
                or hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_sha
                or quote_sha != record["exact_text_sha256"]
                or canonical_sha(locator) != locator_sha
                or locator_sha != record["locator_sha256"]):
                raise IntakeError("evidence_integrity")
            if oid is not None:
                if input_oid != oid:
                    raise IntakeError("evidence_input_mismatch")
                origin = conn.execute(
                    """SELECT output,output_sha256,schema_version
                         FROM source_processing_outputs
                        WHERE project_id=%s AND source_id=%s
                          AND source_version_id=%s AND id=%s""",
                    (pid, sid, vid, oid),
                ).fetchone()
                if (origin is None or origin[1] != input_sha
                    or canonical_sha(origin[0]) != input_sha):
                    raise IntakeError("evidence_origin_integrity")
                if origin[2] in SUPPORTED_OUTPUT_TEXT and not _stored_quote_matches_output(quote, origin[0]):
                    raise IntakeError("evidence_quote_not_in_source")
            else:
                if (pr != e["profile_revision"] or ph != e["profile_hash"]):
                    raise IntakeError("evidence_profile_pin_mismatch")
                origin = conn.execute(
                    """SELECT profile FROM project_profile_versions
                        WHERE project_id=%s AND revision=%s AND profile_hash=%s""",
                    (pid, pr, ph),
                ).fetchone()
                if origin is None:
                    raise IntakeError("profile_missing")
                from intelligence.project_sources.evidence_jobs import _profile_field
                try:
                    field_value = _profile_field(origin[0], locator.get("field_path", ""))
                except Exception as exc:
                    raise IntakeError("evidence_profile_field_missing") from exc
                if (quote not in field_value or hashlib.sha256(field_value.encode("utf-8")).hexdigest() != input_sha):
                    raise IntakeError("evidence_profile_quote_mismatch")

    def resolve_context(self, project_id: str, context_id: str) -> dict[str, Any]:
        with self._read(project_id, context_id) as (_, envelope):
            return envelope

    def resolve_output(self, project_id: str, context_id: str, source_id: str,
                       source_version_id: str, output_id: str, expected_sha256: str) -> dict[str, Any]:
        sid, vid, oid, digest = _uuid(source_id), _uuid(source_version_id), _uuid(output_id), _sha(expected_sha256)
        with self._read(project_id, context_id) as (conn, envelope):
            pins = envelope["context"]["analysis_refs"]
            if not any(
                o["output_id"] == oid and o["source_id"] == sid
                and o["source_version_id"] == vid and o["output_sha256"] == digest for o in pins
            ):
                raise IntakeError("output_not_pinned")
            row = conn.execute(
                """SELECT output FROM source_processing_outputs
                   WHERE project_id=%s AND source_id=%s AND source_version_id=%s
                     AND id=%s AND output_sha256=%s""",
                (project_id, sid, vid, oid, digest),
            ).fetchone()
            if row is None or canonical_sha(row[0]) != digest:
                raise IntakeError("output_integrity")
            return row[0]

    def resolve_evidence(self, project_id: str, context_id: str, evidence_id: str,
                         expected_quote_sha256: str, expected_locator_sha256: str) -> dict[str, Any]:
        eid, qsha, lsha = _uuid(evidence_id), _sha(expected_quote_sha256), _sha(expected_locator_sha256)
        with self._read(project_id, context_id) as (conn, envelope):
            meta = next((x for x in envelope["context"]["evidence_manifest"] if x["evidence_id"] == eid), None)
            if meta is None or meta["exact_text_sha256"] != qsha or meta["locator_sha256"] != lsha:
                raise IntakeError("evidence_not_pinned")
            row = conn.execute(
                """SELECT e.exact_text_or_observation,e.locator,g.citation
                     FROM project_context_grounded_evidence_pins p
                     JOIN project_evidence e ON e.project_id=p.project_id AND e.id=p.evidence_id
                     JOIN project_evidence_grounding g ON g.project_id=p.project_id AND g.evidence_id=p.evidence_id
                   WHERE p.project_id=%s AND p.context_id=%s AND p.evidence_id=%s
                     AND p.quote_sha256=%s AND p.locator_sha256=%s""",
                (project_id, context_id, eid, qsha, lsha),
            ).fetchone()
            if row is None:
                raise IntakeError("evidence_not_pinned")
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != qsha or canonical_sha(row[1]) != lsha:
                raise IntakeError("evidence_integrity")
            return {"evidence_id": eid, "source_id": meta["source_id"],
                    "source_version_id": meta["source_version_id"],
                    "exact_text": row[0], "locator": row[1], "citation": row[2],
                    "trust_label": meta["trust_label"], "review_decision": meta["review_decision"]}

    def resolve_video(self, project_id: str, context_id: str, pin: VideoAnalysisPin) -> dict[str, Any]:
        if not isinstance(pin, VideoAnalysisPin) or pin.schema_version != A_VIDEO_SCHEMA:
            raise IntakeError("invalid_video_pin")
        with self._read(project_id, context_id) as (conn, envelope):
            if pin not in pinned_video_analyses(envelope):
                raise IntakeError("video_not_pinned")
            row = conn.execute(
                """SELECT output FROM source_processing_outputs
                  WHERE project_id=%s AND source_id=%s AND source_version_id=%s
                    AND id=%s AND schema_version=%s AND output_sha256=%s""",
                (project_id, pin.source_id, pin.source_version_id, pin.output_id,
                 pin.schema_version, pin.output_sha256),
            ).fetchone()
            if row is None or canonical_sha(row[0]) != pin.output_sha256:
                raise IntakeError("video_integrity")
            return row[0]

    def inspect(self, project_id: str, context_id: str) -> dict[str, Any]:
        """Readiness only, no source text or unreviewed video observations."""
        with self._read(project_id, context_id) as (_, envelope):
            ctx = envelope["context"]
            excluded = ctx["unavailable_or_excluded_sources"]
            video = [o for o in ctx["analysis_refs"] if o["schema_version"] == A_VIDEO_SCHEMA]
            return {
                "schema_version": "knowledge-intake-report/v1",
                "context_id": envelope["context_id"],
                "context_sha256": envelope["context_sha256"],
                "project_id": envelope["project_id"],
                "approval_decision": envelope["approval"]["decision"],
                "readiness": "approved_partial" if excluded else "ready",
                "sources_total": ctx["coverage"]["sources_total"],
                "source_versions_pinned": sum(s["source_version_id"] is not None for s in ctx["source_manifest"]),
                "excluded_sources": excluded,
                "text_output_refs": sum(o["schema_version"] in SUPPORTED_OUTPUT_TEXT for o in ctx["analysis_refs"]),
                "video_analysis_refs": [o["output_id"] for o in video],
                "evidence_refs": ctx["coverage"]["evidence_total"],
                "warnings": ctx["warnings"],
                "verified": {"database_pins": True, "source_metadata": True,
                             "output_hashes": True, "evidence_hashes": True,
                             "research_file_bytes": self.verify_local_files,
                             "video_media_bytes": False},
                "provider_calls": 0, "database_writes": 0,
                "note": "B1 SQL metadata and fusion output verified; video MP4 bytes not independently checked here. Complete text inventory and B Fact Review belong to later stages.",
            }
