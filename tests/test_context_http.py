import io,json,unittest
from urllib.parse import urlparse
from unittest.mock import patch
from uuid import uuid4
from intelligence.project_sources import context_http as api

PID='a'*32;CID=str(uuid4());URL=f'/api/projects/{PID}/context'
class Handler:
    def __init__(self,data=None):
        raw=json.dumps(data if data is not None else {}).encode()
        self.headers={'Content-Length':str(len(raw))};self.rfile=io.BytesIO(raw)
        self.status=None;self.payload=None
    def json_response(self,status,body):self.status=status;self.payload=body

class API(unittest.TestCase):
    def test_preview_and_list(self):
        with patch.object(api.svc,'preview',return_value={'candidate':True}) as fn:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/draft')))
            self.assertEqual(h.status,200);fn.assert_called_once_with(PID)
        with patch.object(api.svc,'list_snapshots',return_value={'snapshots':[]}) as fn:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/snapshots?offset=2&limit=5')))
            self.assertEqual(h.status,200);fn.assert_called_once_with(PID,offset=2,limit=5)
    def test_no_background_actions_in_http(self):
        with patch.object(api.svc,'create_snapshot',return_value={'created':True,'context_id':CID}) as fn:
            h=Handler({'expected_context_sha256':'a'*64});self.assertTrue(api.handle_post(h,URL+'/build'))
            self.assertEqual(h.status,201);fn.assert_called_once()
        with patch.object(api.svc,'decide',return_value={'decision':'approved'}) as fn:
            h=Handler({'decision':'approved','expected_context_sha256':'a'*64,
                       'acknowledge_partial':False,'excluded_source_ids':[]})
            self.assertTrue(api.handle_post(h,URL+'/snapshots/'+CID+'/decide'))
            self.assertEqual(h.status,201);fn.assert_called_once()
    def test_invalid_query_and_routes(self):
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/draft?unknown=1')));self.assertEqual(h.status,400)
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/compare?left='+CID)));self.assertEqual(h.status,400)
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/snapshots?limit=1&limit=2')));self.assertEqual(h.status,400)
        self.assertFalse(api.handle_get(Handler(),urlparse('/api/other')))
        h=Handler();self.assertTrue(api.handle_post(h,URL+'/nothing'));self.assertEqual(h.status,404)
        h=Handler({'x':1});h.headers['Content-Length']='20001'
        self.assertTrue(api.handle_post(h,URL+'/build'));self.assertEqual(h.status,413)
    def test_snapshot_compare_manifest_selection(self):
        with patch.object(api.svc,'compare',return_value={'profile_changed':False}) as fn:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/compare?left='+CID+'&right='+CID)))
            fn.assert_called_once_with(PID,CID,CID)
        with patch.object(api.svc,'get_snapshot',return_value={'context':{}}) as fn:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/snapshots/'+CID+'/manifest')))
            self.assertTrue(fn.call_args.kwargs['manifest_only'])
        with patch.object(api.svc,'selection',return_value={'selected':[]}) as fn:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/snapshots/'+CID+'/selection?token_budget=500')))
            self.assertEqual(fn.call_args.kwargs['token_budget'],500)

if __name__=='__main__':unittest.main()
