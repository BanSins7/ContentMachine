"""No live HTTP or paid API calls. Strict Sonnet46/OpenRouter web tool contract."""
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from intelligence.project_sources.web_extractor import (
    MODEL, ENDPOINT, WebProblem, public_hostname, provider_request, parse_provider_response, openrouter_fetch)

URL='https://product.acme.org/features'


def provider_payload(status='completed', page='Photo tracking: take a photo to see estimates.'):
    return {'id':'provider_1','choices':[{'finish_reason':'stop','message':{'content':json.dumps({
        'fetch_status':status,'page_url':URL,'title':'Acme','language':'en',
        'page_text':page if status!='unavailable' else '',
        'summary':'Tracks food from photographs.' if status!='unavailable' else '',
        'sections':[{'heading':'Functions','text':'Photo tracking','quote':'Photo tracking'}] if status!='unavailable' else [],
        'claims':[{'kind':'product_function','claim':'Photo tracking','quote':'take a photo'}] if status!='unavailable' else [],
        'limitation':''
    })}}],'usage':{'prompt_tokens':950,'completion_tokens':500,'cost':0.0206}}


class MockResponse:
    status=200
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return json.dumps(self.payload).encode()[:n]

class WebExtractorTests(unittest.TestCase):
    def test_exact_model_and_only_one_domain_allowed(self):
        payload=provider_request(URL)
        self.assertEqual(payload['model'],MODEL)
        self.assertEqual(payload['tools'][0]['type'],'openrouter:web_fetch')
        self.assertEqual(payload['tools'][0]['parameters']['engine'],'openrouter')
        self.assertEqual(payload['tools'][0]['parameters']['allowed_domains'],['product.acme.org'])
        self.assertEqual(payload['tools'][0]['parameters']['max_uses'],1)
        self.assertEqual(payload['max_tokens'],2600)
        self.assertEqual(len(payload['messages']),2)
        self.assertNotIn('openrouter:web_search',str(payload['tools']))

    def test_private_and_malicious_urls_rejected(self):
        for url in ('http://product.acme.org/','https://localhost/','https://127.0.0.1/',
                    'https://[::1]/','https://169.254.169.254/','https://metadata.google.internal/',
                    'https://host.local/','https://user:password@acme.org/','https://acme.org:8443/',
                    'file:///etc/passwd','https://acme.org/a%0d%0aInjected',
                    'https://acme.org\\@127.0.0.1/', 'https://test.invalid/'):
            with self.subTest(url=url),self.assertRaises(WebProblem):public_hostname(url)
        self.assertEqual(public_hostname(URL),'product.acme.org')

    def test_output_marks_model_unverified_and_quote_is_exact(self):
        d=parse_provider_response(provider_payload(),URL)
        self.assertEqual(d['trust_level'],'model_reported_unverified')
        self.assertFalse(d['source_content_verified'])
        self.assertEqual(d['claims'][0]['quote'],'take a photo')
        self.assertEqual(d['page_text'][d['claims'][0]['quote_start']:d['claims'][0]['quote_end']],'take a photo')
        self.assertEqual(d['actual_cost_usd'],'0.0206')

    def test_discard_hallucinated_claim_but_preserve_source_text(self):
        payload=provider_payload()
        obj=json.loads(payload['choices'][0]['message']['content'])
        obj['claims'][0]['quote']='this exact text does not exist'
        payload['choices'][0]['message']['content']=json.dumps(obj)
        self.assertEqual(parse_provider_response(payload,URL)['claims'],[])

    def test_refuse_page_mismatch_and_missing_text(self):
        payload=provider_payload()
        obj=json.loads(payload['choices'][0]['message']['content']);obj['page_url']='https://evil.example.net/'
        payload['choices'][0]['message']['content']=json.dumps(obj)
        with self.assertRaises(WebProblem):parse_provider_response(payload,URL)
        payload=provider_payload(page='')
        with self.assertRaises(WebProblem):parse_provider_response(payload,URL)

    def test_unavailable_never_fabricates_segments(self):
        result=parse_provider_response(provider_payload('unavailable'),URL)
        self.assertEqual(result['fetch_status'],'unavailable')
        self.assertEqual(result['sections'],[])
        self.assertIsNone(result['page_text_sha256'])

    def test_truncated_content_rejected(self):
        payload=provider_payload();payload['choices'][0]['finish_reason']='length'
        with self.assertRaises(WebProblem):parse_provider_response(payload,URL)

    def test_only_openrouter_is_network_destination(self):
        requests=[]
        def opener(request,timeout):
            requests.append(request)
            self.assertEqual(request.full_url,ENDPOINT)
            self.assertEqual(request.get_method(),'POST')
            self.assertIn(URL,request.data.decode())
            self.assertEqual(timeout,100)
            return MockResponse(provider_payload())
        response=openrouter_fetch(URL,'secret',opener=opener)
        self.assertEqual(len(requests),1)
        self.assertEqual(response['fetch_status'],'completed')

    def test_no_key_no_network(self):
        with self.assertRaises(WebProblem) as context:openrouter_fetch(URL,'',opener=lambda *a,**k:None)
        self.assertEqual(context.exception.code,'missing_api_key')

    def test_provider_network_errors_no_made_up_success(self):
        def bad(req,timeout):raise URLError('error')
        with self.assertRaises(WebProblem) as ctx:openrouter_fetch(URL,'secret',opener=bad)
        self.assertEqual(ctx.exception.code,'provider_network')

if __name__=='__main__':unittest.main()
