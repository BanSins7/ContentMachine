"""PIE-MOD-26 synthetic redacted registry queries; no real UAE rights claimed."""
from __future__ import annotations
import csv
import io
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
from intelligence.patterns_insights.research.pattern_queries import (
    RegistryQuery,CursorSigner,PatternQueryPage,RedactedPattern,
    query_pattern_registry,export_redacted_pattern_page,_rights_revalidate,
    _platform_from_videos)
from intelligence.patterns_insights.research.pattern_registry import build_candidate_pattern_registry_batch
from test_pie_pattern_registry import fixture as fixture25

NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)
SECRET=b'pie-synthetic-hmac-secret-never-use-in-production-v1'


def sample():
    sources=fixture25()
    with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
               return_value=sources[13]):
        result=build_candidate_pattern_registry_batch(*sources)
    return sources,result


def row(entry):
    return (entry.series_id,1,entry.decision,entry.hypothesis_sha256,
            entry.evidence_sha256,entry.payload_sha256,entry.snapshot_sha256,
            entry.quality_sha256,entry.source_rows_sha256,entry.as_dict())


class FakeCursor:
    def __init__(self,rows):
        self.rows=rows
        self.calls=[]
    def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if 'statement_timestamp()' in sql:
            self.fetched=[(NOW,)];return self
        if 'SELECT id,platform FROM videos' in sql:
            self.fetched=[(v,'youtube') for v in params[0]];return self
        (project,p2,owner,watermark,last,h1,h2,s1,s2,b1,b2,min_n,min_bins,limit)=params
        found=[]
        for entry in self.rows:
            (sid,rev,status,hyp,evidence,sh,snapshot,qual,rsh,payload)=entry
            if (payload['project_id']!=project or project!=p2 or
                payload['owner_id']!=owner or sid<=last or
                h1 is not None and h1!=hyp or
                s1 is not None and s1!=status or
                b1 is not None and (payload['hypothesis_specification']['feature_family']!='b' or
                                    payload['hypothesis_specification']['feature_name']!=b1) or
                payload['matched_pair_count']<min_n or
                sum(x.get('supported') is True for x in payload['statistical_summary']['bin_coverage'])<min_bins):
                continue
            found.append(entry)
        self.fetched=sorted(found,key=lambda x:x[0])[:limit]
        return self
    def fetchone(self):return self.fetched[0]
    def fetchall(self):return self.fetched


class FakeAccess:
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=[]
    def require_current_project_access(self,project_id,principal_id):
        self.calls.append((project_id,principal_id))
        return self.allowed


class NoUAE:
    def read_fresh_qualified(self,project,manifest):
        raise RuntimeError('Unavailable live producer')


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.sources,self.batch=sample()
        self.project=self.sources[0].project_id
        self.owner=self.sources[14].owner_id
        self.query=RegistryQuery(self.project,self.owner,page_size=2)
        self.signer=CursorSigner(SECRET)
        self.db=FakeCursor([row(x) for x in self.batch.entries])
        self.auth=FakeAccess()
        self.gate=SourceEligibilityGate()
        self.reader=NoUAE()

    def reject(self,code,func,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as e:func(*args,**kwargs)
        self.assertEqual(e.exception.code,code)

    def read(self,query=None,rights=True,db=None,auth=None,platform='youtube'):
        with patch('intelligence.patterns_insights.research.pattern_queries._rights_revalidate',
                   return_value=rights),patch(
             'intelligence.patterns_insights.research.pattern_queries._platform_from_videos',
                   return_value=platform):
            return query_pattern_registry(db or self.db,query or self.query,auth or self.auth,
                self.reader,self.gate,self.signer,now=NOW)

    def test_first_page_two_items_signed_cursor(self):
        result=self.read()
        self.assertEqual(len(result.items),2)
        self.assertIsNotNone(result.next_cursor)
        self.assertEqual(result.project_id,self.project)
        self.assertTrue(result.source_rights_revalidated)

    def test_two_three_pages_cover_all_five_unique_series(self):
        query=self.query
        seen=[]
        for _ in range(4):
            page=self.read(query)
            seen.extend(x.series_id for x in page.items)
            if page.next_cursor is None:break
            query=replace(query,cursor=page.next_cursor)
        self.assertEqual(seen,sorted(e.series_id for e in self.batch.entries))
        self.assertEqual(len(seen),5)

    def test_project_permission_checked_before_sql(self):
        db=FakeCursor(self.db.rows)
        auth=FakeAccess(False)
        self.reject('registry_project_access_denied',
                    query_pattern_registry,db,self.query,auth,
                    self.reader,self.gate,self.signer,now=NOW)
        self.assertEqual(db.calls,[])
        self.assertEqual(auth.calls,[(self.project,self.owner)])

    def test_every_page_rechecks_current_membership(self):
        first=self.read()
        self.auth.allowed=False
        self.reject('registry_project_access_denied',self.read,
                    replace(self.query,cursor=first.next_cursor))
        self.assertEqual(len(self.auth.calls),2)

    def test_missing_current_rights_gate_refuses_query_before_sql(self):
        self.reject('registry_rights_unavailable',query_pattern_registry,
                    self.db,self.query,self.auth,self.reader,None,self.signer,now=NOW)
        self.assertEqual(self.db.calls,[])

    def test_no_real_UAE_reader_means_no_returned_rows(self):
        p=query_pattern_registry(self.db,self.query,self.auth,
                                 self.reader,self.gate,self.signer,now=NOW)
        self.assertEqual(p.items,())
        self.assertTrue(p.next_cursor)

    def test_revocation_suppresses_entire_page(self):
        result=self.read(rights=False)
        self.assertEqual(result.items,())
        self.assertNotIn('denied_count',result.as_dict())
        self.assertNotIn('source_rows_sha256',json.dumps(result.as_dict()))

    def test_revocation_between_pages_prevents_old_access(self):
        first=self.read()
        after=self.read(replace(self.query,cursor=first.next_cursor),rights=False)
        self.assertEqual(len(first.items),2)
        self.assertEqual(after.items,())

    def test_safe_response_excludes_raw_video_and_analysis_fingerprints(self):
        result=self.read().as_dict()
        raw=json.dumps(result,ensure_ascii=False)
        for sensitive in ('source_discovery_pairs','high_video_id','low_video_id',
                          'source_rows_sha256','uae_snapshot_id','dataset_manifest_sha256',
                          'creator_key','source_technical_numeric_sha256'):
            self.assertNotIn(sensitive,raw)
        self.assertEqual(len(result['items']),2)
        for item in result['items']:
            self.assertIsNone(item['inferential_p_value'])
            self.assertFalse(item['publication_allowed'])
            self.assertFalse(item['causal_claim'])

    def test_csv_json_are_redacted_and_roundtrip(self):
        page=self.read()
        self.assertEqual(json.loads(export_redacted_pattern_page(page,'json')),page.as_dict())
        csv_text=export_redacted_pattern_page(page,'csv')
        rows=list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(len(rows),2)
        self.assertNotIn('source_discovery_pairs',csv_text)
        self.assertNotIn('source_rows_sha256',csv_text)

    def test_unchecked_page_export_denied(self):
        self.reject('invalid_registry_export',export_redacted_pattern_page,
                    PatternQueryPage(self.project,(),None,False),'json')
        self.reject('invalid_registry_export',export_redacted_pattern_page,self.read(),'xlsx')

    def test_cursor_changes_produce_crypto_refusal(self):
        page=self.read()
        encoded=page.next_cursor
        tampered=encoded[:12]+('A' if encoded[12]!='A' else 'B')+encoded[13:]
        self.reject('registry_cursor_tampered',self.read,
                    replace(self.query,cursor=tampered))

    def test_cursor_binding_all_filters_and_actor(self):
        page=self.read()
        for q in (replace(self.query,principal_id='other_reader'),
                  replace(self.query,project_id='f'*32 if self.project!='f'*32 else 'e'*32),
                  replace(self.query,b_feature_name='cuts_per_minute_x100'),
                  replace(self.query,min_matched_pairs=3),
                  replace(self.query,status='rejected')):
            self.reject('registry_cursor_scope_mismatch',self.read,
                        replace(q,cursor=page.next_cursor))

    def test_cursor_stale_and_different_signer(self):
        token=self.read().next_cursor
        self.reject('registry_cursor_expired',self.signer.parse,
                    token,self.query.fingerprint(),NOW+timedelta(minutes=16))
        self.reject('registry_cursor_tampered',CursorSigner(b'x'*32).parse,
                    token,self.query.fingerprint(),NOW)

    def test_malformed_cursor_fails_closed(self):
        self.reject('invalid_registry_cursor',self.read,
                    replace(self.query,cursor='?????'))
        self.reject('invalid_registry_cursor_key',CursorSigner,b'abc')

    def test_B_feature_hypothesis_status_min_n_bin_support(self):
        candidate=self.batch.entries[3]
        name=candidate.as_dict()['hypothesis_specification']['feature_name']
        q=replace(self.query,b_feature_name=name,
                  hypothesis_sha256=candidate.hypothesis_sha256,
                  status=candidate.decision,min_matched_pairs=4,
                  min_supported_bins=2,page_size=30)
        p=self.read(q)
        self.assertEqual([x.factor_id for x in p.items],[candidate.factor_id])
        self.assertEqual(p.items[0].feature_family,'b')

    def test_requested_platform_mismatch_suppresses(self):
        result=self.read(replace(self.query,platform='instagram'),platform=False)
        self.assertEqual(result.items,())

    def test_query_cannot_request_supported_all_revisions_or_raw(self):
        for kw in (dict(status='supported'),dict(view='all_revisions'),
                   dict(redaction='include_original_video_ids'),
                   dict(page_size=0),dict(page_size=81),
                   dict(min_matched_pairs=True),dict(min_supported_bins=17),
                   dict(b_feature_name='B with spaces'),dict(platform='?')):
            self.reject('invalid_registry_query',RegistryQuery,self.project,self.owner,**kw)

    def test_project_and_owner_predicates_in_sql(self):
        self.read()
        sql,params=self.db.calls[1]
        self.assertIn('r.project_id=%s AND s.project_id=%s',sql)
        self.assertIn('s.owner_id=%s',sql)
        self.assertIn('DISTINCT ON (r.series_id)',sql)
        self.assertEqual(params[:3],(self.project,self.project,self.owner))

    def test_status_filter_runs_after_latest_revision_selection(self):
        self.read()
        sql=self.db.calls[1][0]
        self.assertLess(sql.index('ORDER BY r.series_id,r.revision DESC'),
                        sql.index('AND (%s::text IS NULL OR status=%s)'))

    def test_wrong_project_or_rehashed_payload_refused(self):
        rows=sorted(self.db.rows,key=lambda x:x[0])
        for update in ('project','fingerprint'):
            copy=list(rows)
            target=list(copy[0])
            candidate=dict(target[-1])
            if update=='project':
                candidate['project_id']='f'*32
            else:
                candidate['statistical_summary']=dict(candidate['statistical_summary'])
                candidate['statistical_summary']['monotonicity']='forged'
            target[-1]=candidate
            copy[0]=tuple(target)
            self.reject('registry_untrusted_row',self.read,db=FakeCursor(copy))

    def test_no_query_on_unapproved_principal_type(self):
        self.reject('invalid_registry_query',RegistryQuery,self.project,'bad actor')
        self.reject('invalid_registry_query',RegistryQuery,'not_a_project',self.owner)


class CurrentEvidenceAndPlatformTests(unittest.TestCase):
    def setUp(self):
        sources,batch=sample()
        self.project=sources[0].project_id
        self.payload=batch.entries[0].as_dict()
        self.pairs=self.payload['source_discovery_pairs']

    def _fake_read(self,drop=None,alter=None):
        observed=[]
        for pair in self.pairs:
            for role in ('high','low'):
                i=pair[f'{role}_video_id']
                if i==drop:continue
                row_hash=pair[f'{role}_row_sha256']
                if i==alter:row_hash='f'*64
                observed.append(type('Obs',(),dict(video_id=i,row_sha256=row_hash,
                              uae_snapshot_id=pair[f'{role}_uae_snapshot_id']))())
        pointer=type('Pointer',(),dict(project_id=self.project,
                uae_manifest_sha256=self.payload['dataset_manifest_sha256']))()
        dataset=type('Dataset',(),dict(pointer=pointer,
                manifest_sha256=self.payload['dataset_manifest_sha256'],
                observations=tuple(observed)))()
        result=type('Read',(),dict(dataset=dataset))()
        reader=type('FakeUAE',(),{
            'read_fresh_qualified':lambda _,project,manifest:result})()
        return reader,result

    def test_full_original_row_revalidation_and_missing_row_denied(self):
        import intelligence.patterns_insights.research.pattern_queries as mod
        reader,result=self._fake_read()
        report=type('Reported',(),dict(candidate_eligible=True,suppressed_rows=0,
               structurally_eligible_rows=8,total_rows=8,
               manifest_sha256=self.payload['dataset_manifest_sha256']))()
        with patch.object(mod,'UAEReadResult',type(result)),patch.object(
            SourceEligibilityGate,'evaluate',return_value=report):
            self.assertTrue(_rights_revalidate(self.payload,reader,SourceEligibilityGate(),self.project))
            omitted=self.pairs[0]['high_video_id']
            self.assertFalse(_rights_revalidate(self.payload,self._fake_read(drop=omitted)[0],
                             SourceEligibilityGate(),self.project))
            self.assertFalse(_rights_revalidate(self.payload,self._fake_read(alter=omitted)[0],
                             SourceEligibilityGate(),self.project))

    def test_unconnected_real_gate_never_qualifies_synthetic_rights(self):
        reader,_=self._fake_read()
        self.assertFalse(_rights_revalidate(self.payload,reader,
                                            SourceEligibilityGate(),self.project))

    def test_platform_all_paired_rows_required_and_homogeneous(self):
        ids=[p[f'{role}_video_id'] for p in self.pairs for role in ('high','low')]
        class VideoDB:
            def __init__(self,rows):self.rows=rows
            def execute(self,sql,params):return self
            def fetchall(self):return self.rows
        self.assertEqual(_platform_from_videos(VideoDB([(v,'youtube') for v in ids]),
                                               self.pairs,'youtube'),'youtube')
        self.assertIs(_platform_from_videos(VideoDB([(v,'youtube') for v in ids[:-1]]),
                                           self.pairs,'youtube'),False)
        mixed=[(v,'youtube' if i%2 else 'instagram') for i,v in enumerate(ids)]
        self.assertIs(_platform_from_videos(VideoDB(mixed),self.pairs,None),False)


if __name__=='__main__':
    unittest.main()
