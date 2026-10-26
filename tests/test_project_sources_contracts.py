"""Stage 01 DTO tests: fast, offline, no user data or provider calls."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from intelligence.project_sources.contracts import (
    DTO_TYPES, json_schemas, ProjectSource, SourceVersion, MediaAsset, ExtractionJob,
    ExtractedSegment, EvidenceRecord, VideoAnalysis, ProjectContextSnapshot,
    RightsPolicy, Locator, VideoSpan, SourcePin, ProcessingStatus,
)

PROJECT = 'a' * 32
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
DIGEST = 'b' * 64


class Stage01Contracts(unittest.TestCase):
    def samples(self):
        s, v, a, j, seg, ev, vid, ctx = [uuid4() for _ in range(8)]
        locator = Locator(kind='questionnaire', field_path='product_functions', start_char=1, end_char=10)
        return [
            ProjectSource(source_id=s, project_id=PROJECT, source_type='questionnaire_field',
                          original_input='A function', created_at=NOW, updated_at=NOW),
            SourceVersion(source_version_id=v, source_id=s, project_id=PROJECT, version_no=1,
                          content_sha256=DIGEST, byte_size=35, recorded_at=NOW),
            MediaAsset(asset_id=a, project_id=PROJECT, source_id=s, source_version_id=v,
                       asset_kind='document', content_sha256=DIGEST, byte_size=35,
                       media_type='application/pdf', storage_ref='objects/file.pdf', created_at=NOW),
            ExtractionJob(job_id=j, project_id=PROJECT, source_id=s, source_version_id=v,
                          stage='document_parse', idempotency_key='parse-v1', created_at=NOW, updated_at=NOW),
            ExtractedSegment(segment_id=seg, project_id=PROJECT, source_id=s, source_version_id=v,
                             text='Actual source text', locator=locator, extraction_engine='pdf',
                             extraction_version='v1', created_at=NOW),
            EvidenceRecord(evidence_id=ev, project_id=PROJECT, source_id=s, source_version_id=v,
                           evidence_kind='user_claim', exact_text_or_observation='A function',
                           locator=locator, extraction_engine='intake', extraction_version='1', created_at=NOW),
            VideoAnalysis(analysis_id=vid, project_id=PROJECT, source_id=s, source_version_id=v,
                          canonical_video_url='https://example.com/v', media_sha256=DIGEST,
                          duration_ms=1000, transcript_status=ProcessingStatus.unavailable, coverage=Decimal('0.4'),
                          scenes=[VideoSpan(start_ms=0, end_ms=500, frame_refs=['f1'])],
                          status=ProcessingStatus.partial),
            ProjectContextSnapshot(context_id=ctx, project_id=PROJECT, version=1,
                                   immutable_context_hash=DIGEST, pinned_profile_revision=1,
                                   pinned_profile_hash=DIGEST,
                                   pinned_source_versions=[SourcePin(source_id=s, source_version_id=v,
                                                                      content_sha256=DIGEST, status=ProcessingStatus.completed)],
                                   created_at=NOW),
        ]

    def test_all_eight_strict_dtos_roundtrip_and_json_schema(self):
        schemas = json_schemas()
        self.assertEqual(set(schemas), {c.__name__ for c in DTO_TYPES})
        for sample in self.samples():
            with self.subTest(model=type(sample).__name__):
                self.assertEqual(sample, sample.json_round_trip())
                self.assertFalse(schemas[type(sample).__name__].get('additionalProperties', True))
                with self.assertRaises(ValidationError):
                    type(sample).model_validate({**sample.model_dump(), 'unknown_key': 123})
                with self.assertRaises(ValidationError):
                    type(sample).model_validate({**sample.model_dump(), 'project_id': 'x' * 32})

    def test_long_document_segments_and_bounded_evidence(self):
        sample = self.samples()[4]
        self.assertEqual(len(ExtractedSegment.model_validate({**sample.model_dump(),
            "text": "A" * 12000}).text), 12000)
        with self.assertRaises(ValidationError):
            ExtractedSegment.model_validate({**sample.model_dump(), "text": "A" * 20001})

    def test_permissions_deny_by_default(self):
        self.assertEqual(RightsPolicy().model_dump(), {
            'can_fetch': False, 'can_store': False, 'can_analyze': False,
            'can_send_to_llm': False, 'retention_days': None, 'basis': None})
        with self.assertRaises(ValidationError):
            RightsPolicy(can_fetch='true')

    def test_url_and_time_validation(self):
        s = self.samples()[0]
        for url in ('http://example.com', 'https://user@example.com/',
                    'https://example.com:8080/', 'file:///private', 'https://example.com/#fragment'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                ProjectSource.model_validate({**s.model_dump(), 'canonical_url': url})
        with self.assertRaises(ValidationError):
            ProjectSource.model_validate({**s.model_dump(), 'updated_at': datetime(2020, 1, 1, tzinfo=timezone.utc)})
        with self.assertRaises(ValidationError):
            ProjectSource.model_validate({**s.model_dump(), 'created_at': datetime(2026, 1, 1)})

    def test_provenance_ranges_are_required(self):
        for invalid in (
            dict(kind='questionnaire', field_path='product_functions'),
            dict(kind='video'),
            dict(kind='pdf'),
            dict(kind='spreadsheet', sheet='Sheet1'),
            dict(kind='text', start_char=5, end_char=5),
            dict(kind='video', start_ms=11, end_ms=10),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Locator(**invalid)
        self.assertEqual(Locator(kind='video', start_ms=0, end_ms=10).start_ms, 0)

    def test_paid_job_requires_consent_and_bounds_attempts(self):
        sample = self.samples()[3]
        with self.assertRaises(ValidationError):
            ExtractionJob.model_validate({**sample.model_dump(), 'cost_estimate_usd': Decimal('1.0')})
        with self.assertRaises(ValidationError):
            ExtractionJob.model_validate({**sample.model_dump(), 'attempts': 4, 'max_attempts': 3})
        self.assertIsNotNone(ExtractionJob.model_validate({**sample.model_dump(),
            'cost_estimate_usd': Decimal('1'), 'consent_ref': 'owner-approved'}))

    def test_source_versions_no_false_asset(self):
        version = self.samples()[1]
        with self.assertRaises(ValidationError):
            SourceVersion.model_validate({**version.model_dump(), 'byte_size': None})
        with self.assertRaises(ValidationError):
            SourceVersion.model_validate({**version.model_dump(), 'content_sha256': 'not_sha256'})

    def test_inference_is_never_implicitly_approved(self):
        ev = self.samples()[5]
        with self.assertRaises(ValidationError):
            EvidenceRecord.model_validate({**ev.model_dump(), 'evidence_kind': 'model_inference'})
        self.assertEqual(EvidenceRecord.model_validate({**ev.model_dump(),
            'evidence_kind': 'model_inference', 'review_status': 'needs_review'}).review_status, 'needs_review')

    def test_video_span_and_context_pin_guards(self):
        vid = self.samples()[6]
        with self.assertRaises(ValidationError):
            VideoAnalysis.model_validate({**vid.model_dump(), 'scenes': [{'start_ms': 0, 'end_ms': 2000}]})
        ctx = self.samples()[7]
        with self.assertRaises(ValidationError):
            ProjectContextSnapshot.model_validate({**ctx.model_dump(),
                'pinned_source_versions': [ctx.pinned_source_versions[0].model_dump()] * 2})
        with self.assertRaises(ValidationError):
            ProjectContextSnapshot.model_validate({**ctx.model_dump(), 'approval_status': 'approved'})


if __name__ == '__main__':
    unittest.main()
