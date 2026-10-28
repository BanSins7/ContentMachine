"""PIE-MOD-34: fail-closed HTTP, source scope, synthetic UI projections.

Rights/query-port mocks are explicit; no real UAE entitlement or PG17 claimed.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timezone
from urllib.parse import urlparse
from unittest.mock import patch
import os
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError
from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
from intelligence.patterns_insights.research.pattern_queries import (
    RedactedPattern,PatternQueryPage,CursorSigner,
)
from intelligence.patterns_insights.explorer import (
    ExplorerPorts,explore,explore_for_request,
)
from intelligence.patterns_insights.http_api import handle_get,handle_post

PROJECT='a'*32
ACTOR='verified-owner'
SERIES='b'*64
HYP='c'*64
GEN='d'*64
NOW=datetime(2026,10,28,13,tzinfo=timezone.utc)


def pattern(revision=2):
    return RedactedPattern(
        series_id=SERIES,hypothesis_sha256=HYP,revision=revision,
        factor_id='factor_one',feature_family='b',feature_name='duration',
        metric_kind='duration',platform='youtube',status='exploratory',
        matched_pair_count=4,supported_bin_count=2,
        descriptive_direction='nondecreasing_descriptive',
        descriptive_raw_mean='5/2',descriptive_source_unit='ms',
        description='Exploratory matched-pair association, not causal',
    )


class Actor:
    def __init__(self,principal=ACTOR):self.principal=principal;self.calls=[]
    def verified_project_actor(self,handler,project):
        self.calls.append((handler,project))
        return self.principal


class Authority:
    def __init__(self,allowed=True):self.allowed=allowed;self.calls=[]
    def require_current_project_access(self,project,actor):
        self.calls.append((project,actor))
        return self.allowed


class Reader:
    def read_fresh_qualified(self,project,manifest):raise RuntimeError('Mock no live UAE')


class DB:
    def __init__(self):self.calls=[];self.summary=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        class Cursor:
            def __init__(self,rows):self.rows=rows
            def fetchall(self):return self.rows
        return Cursor(self.summary)


class Context:
    def __init__(self,db):self.db=db
    def __enter__(self):return self.db
    def __exit__(self,*a):return False


class Handler:
    def __init__(self):self.responses=[];self.headers={}
    def json_response(self,status,data):self.responses.append((status,data))


class ExplorerTest(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.actor=Actor();self.authority=Authority()
        self.reader=Reader();self.handler=Handler()
        self.connects=[]
        def connect():
            self.connects.append(True)
            return Context(self.db)
        self.ports=ExplorerPorts(self.actor,connect,self.authority,
                                 self.reader,SourceEligibilityGate(),CursorSigner(b'k'*40))
        self.page=PatternQueryPage(PROJECT,(pattern(),),None,True)
        self.patcher=patch('intelligence.patterns_insights.explorer.query_pattern_registry',
                           side_effect=self.safe_query)
        self.mock=self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.queries=[];self.now=NOW
    def safe_query(self,connection,query,authorizer,reader,gate,signer,*,now=None):
        self.queries.append(query)
        if not self.authority.allowed:
            raise PIEBoundaryError('registry_project_access_denied','No rights')
        if self.page is None:
            raise PIEBoundaryError('registry_rights_unavailable','Unavailable')
        return self.page
    def uri(self,view='patterns',params=''):
        suffix=('?'+params) if params else ''
        return urlparse('/api/projects/'+PROJECT+'/pie/'+view+suffix)
    def http(self,view='patterns',params=''):
        self.handler.responses.clear()
        assert handle_get(self.handler,self.uri(view,params),ports=self.ports)
        return self.handler.responses[-1]
    def deny(self,code,func,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as exc:func(*args,**kwargs)
        self.assertEqual(exc.exception.code,code)

    def test_positive_pattern_page_exact_safe_metadata(self):
        status,data=self.http()
        self.assertEqual(status,200)
        self.assertEqual(data['schema_version'],'pie-pattern-explorer/v1')
        self.assertEqual(data['project_id'],PROJECT)
        self.assertEqual(data['items'][0]['matched_pair_count'],4)
        self.assertEqual(data['items'][0]['descriptive_raw_mean'],'5/2')
        self.assertEqual(data['items'][0]['descriptive_source_unit'],'ms')
        self.assertIsNone(data['items'][0]['inferential_p_value'])
        self.assertFalse(data['publication_allowed'])
        self.assertFalse(data['items'][0]['causal_claim'])

    def test_source_actor_is_server_resolved_not_from_header(self):
        self.handler.headers['X-Actor-Id']='forged-source-owner'
        self.http()
        self.assertEqual(self.queries[-1].principal_id,ACTOR)
        self.assertEqual(self.actor.calls[-1][1],PROJECT)
        self.assertEqual(self.authority.calls[-1],(PROJECT,ACTOR))

    def test_project_membership_denial_prevents_sql(self):
        self.authority.allowed=False
        status,data=self.http()
        self.assertEqual(status,403)
        self.assertEqual(self.db.calls,[])
        self.assertEqual(self.queries,[])
        self.assertNotIn('verified-owner',str(data))

    def test_missing_identity_rejected_even_on_local_host(self):
        self.actor.principal=None
        status,data=self.http()
        self.assertEqual(status,403)
        self.assertFalse(self.connects)
        self.assertFalse(data['publication_allowed'])

    def test_identity_exception_fails_closed(self):
        self.actor.verified_project_actor=lambda *_: (_ for _ in ()).throw(RuntimeError('missing'))
        self.assertEqual(self.http()[0],403)

    def test_unknown_no_ports_fail_closed_even_when_path_valid(self):
        h=Handler()
        assert handle_get(h,self.uri(),ports=None)
        self.assertEqual(h.responses[0][0],503)
        self.assertEqual(h.responses[0][1]['state'],'not_ready')

    def test_bad_project_route_returns_not_found_without_sql(self):
        handler=Handler()
        self.assertTrue(handle_get(handler,urlparse('/api/projects/fake/pie/patterns'),
                                   ports=self.ports))
        self.assertEqual(handler.responses[-1][0],404)
        self.assertEqual(self.queries,[])

    def test_pattern_collection_safe_without_provenance_source_rows(self):
        status,data=self.http()
        blob=str(data)
        for secret in ('source_discovery_pairs','high_video_id',
                       'source_rows_sha256','creator_key','uae_snapshot_id',
                       'api_key','raw_model_text'):
            self.assertNotIn(secret,blob)

    def test_all_read_views_currently_require_rights(self):
        for view in ('overview','patterns','datasets','candidates',
                     'validations','results','insights','reviews'):
            self.http(view)
        self.assertEqual(len(self.queries),8)
        self.assertEqual(len(self.actor.calls),8)

    def test_datsets_no_original_manifest_or_row_ever_exported(self):
        status,data=self.http('datasets')
        self.assertEqual(status,200)
        self.assertEqual(data['state'],'not_ready')
        self.assertEqual(data['items'],[])
        self.assertFalse(data['publication_allowed'])

    def test_overview_aggregates_are_only_current_page(self):
        status,d=self.http('overview')
        self.assertEqual(status,200)
        self.assertEqual(d['meta']['matched_pair_count_on_page'],4)
        self.assertEqual(d['meta']['candidate_status_counts_on_page']['exploratory'],1)
        self.assertTrue(d['page_scope_only'])

    def test_results_do_not_invent_significance_or_holdout(self):
        status,data=self.http('results')
        self.assertEqual(status,200)
        self.assertIsNone(data['items'][0]['p_value'])
        self.assertIsNone(data['items'][0]['confidence_interval'])
        self.assertFalse(data['items'][0]['independent_holdout_confirmed'])

    def test_insight_and_review_summaries_hide_original_draft_and_reviewer(self):
        self.db.summary=[(SERIES,GEN,2,'candidate_checked_for_review',
                          'exact_source_consistent',2)]
        for view in ('insights','reviews'):
            _,data=self.http(view)
            item=data['items'][0]
            self.assertEqual(item['internal_review_event_count'],2)
            self.assertFalse(item['insight_text_available'])
            self.assertFalse(item['can_publish'])
            self.assertNotIn('summary',str(item))
            self.assertNotIn('reviewer_id',str(item))

    def test_stale_generation_revision_is_suppressed_even_if_db_returns(self):
        self.db.summary=[(SERIES,GEN,1,'candidate_checked_for_review',None,5)]
        _,data=self.http('insights')
        self.assertEqual(data['items'],[])
        self.assertEqual(data['state'],'not_ready')

    def test_undisclosed_series_is_suppressed(self):
        self.db.summary=[('e'*64,GEN,2,'candidate_checked_for_review',None,4)]
        self.assertEqual(self.http('reviews')[1]['items'],[])

    def test_revoked_rights_empty_page_hides_all_sources(self):
        self.page=PatternQueryPage(PROJECT,(),None,True)
        data=self.http('patterns')[1]
        self.assertEqual(data['items'],[])
        self.assertEqual(data['state'],'not_ready')
        self.assertFalse(data['publication_allowed'])

    def test_unavailable_source_rights_returns_generic_not_ready(self):
        self.page=None
        status,data=self.http()
        self.assertEqual(status,503)
        self.assertEqual(data['state'],'not_ready')
        self.assertNotIn('high_video_id',str(data))

    def test_server_rechecks_every_page_even_with_cursor(self):
        self.http('patterns','page_size=10')
        self.http('patterns','page_size=10&cursor=abc')
        self.assertEqual(self.queries[-1].cursor,'abc')
        self.assertEqual(len(self.actor.calls),2)

    def test_unknown_filter_and_duplicate_filters_denied(self):
        for q in ('owner_id=someone','status=exploratory&status=rejected',
                  'page_size=1000','min_matched_pairs=-1','min_supported_bins=99'):
            self.assertEqual(self.http('patterns',q)[0],400,q)

    def test_bad_view_path_returns_404_without_read(self):
        h=Handler()
        assert handle_get(h,self.uri('raw_drafts'),ports=self.ports)
        self.assertEqual(h.responses[-1][0],404)
        self.assertEqual(self.queries,[])

    def test_post_methods_are_unconditionally_denied(self):
        h=Handler()
        self.assertTrue(handle_post(h,self.uri().path))
        self.assertEqual(h.responses[-1][0],405)
        self.assertFalse(handle_post(h,'/api/projects/'+PROJECT+'/update'))

    def test_response_is_empty_if_no_eligible_source(self):
        self.page=PatternQueryPage(PROJECT,(),None,True)
        self.assertEqual(self.http('candidates')[1]['items'],[])

    def test_query_source_page_not_rights_verified_rejected(self):
        self.page=PatternQueryPage(PROJECT,(pattern(),),None,False)
        status,data=self.http()
        self.assertEqual(status,503)
        self.assertEqual(data['state'],'not_ready')

    def test_project_mismatch_rejected_after_page(self):
        self.page=PatternQueryPage('e'*32,(pattern(),),None,True)
        self.assertEqual(self.http()[0],503)

    def test_bad_user_filters_cannot_override_principal(self):
        self.assertEqual(self.http('patterns','principal_id=forged')[0],400)

    def test_invalid_port_object_fails_closed_before_sql(self):
        self.deny('pie_ports_unavailable',explore_for_request,
                  self.handler,PROJECT,'patterns',{},None)

    def test_current_source_membership_refreshed_per_view(self):
        self.http('patterns');self.authority.allowed=False
        self.assertEqual(self.http('results')[0],403)

    def test_current_source_rights_revoke_between_requests(self):
        self.http('patterns');self.page=PatternQueryPage(PROJECT,(),None,True)
        self.assertEqual(self.http('patterns')[1]['state'],'not_ready')

    def test_pagination_scope_does_not_claim_full_corpus_totals(self):
        page=PatternQueryPage(PROJECT,(pattern(),),'opaque-next',True)
        self.page=page
        data=self.http('overview')[1]
        self.assertTrue(data['page_scope_only'])
        self.assertEqual(data['next_cursor'],'opaque-next')

    def test_malformed_raw_query_actor_rejected(self):
        self.deny('pie_invalid_query',explore,
                  self.db,PROJECT,'patterns','bad actor',{},self.ports)

    def test_unavailable_sql_summaries_fail_generic_without_raw_data(self):
        class BrokenDB:
            def execute(self,*a,**k):raise RuntimeError('internal source secret')
        with patch('intelligence.patterns_insights.explorer.query_pattern_registry',
                   return_value=self.page):
            with self.assertRaises(PIEBoundaryError) as raised:
                explore_for_request(self.handler,PROJECT,'insights',{},replace(
                    self.ports,connect=lambda:Context(BrokenDB())))
            self.assertEqual(raised.exception.code,'pie_data_unavailable')


if __name__=='__main__':
    unittest.main()
