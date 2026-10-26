"""Spawn smoke and parser failure isolation on supported platforms."""
import unittest
from unittest.mock import patch
from intelligence.project_sources.document_jobs import isolated_extract
from intelligence.project_sources.document_parser import ParseFailure

class ParserIsolation(unittest.TestCase):
    def test_safe_subprocess_result(self):
        out=isolated_extract('Текст с русскими буквами'.encode('utf-8'),'data.txt',{'ocr':False},timeout=25)
        self.assertEqual(out['status'],'completed')
        self.assertIn('русскими',out['segments'][0]['text'])

    def test_subprocess_failure_does_not_claim_success(self):
        with self.assertRaises(ParseFailure):
            isolated_extract(b'bad content','invalid.pdf',{'ocr':False},timeout=25)

if __name__=='__main__':unittest.main()
