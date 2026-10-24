"""Offline stage 03 parser gates and crash-safety invariants."""
import base64
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from intelligence.project_sources import file_library as files


def zip_content(name,data=b'<?xml version="1.0"?><doc/>'):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr(name,data)
    return buf.getvalue()

class FileLibraryUnit(unittest.TestCase):
    def test_supported_signatures_and_parser_eligibility(self):
        good={
            'a.pdf':b'%PDF-1.7\nhello',
            'a.docx':zip_content('word/document.xml'),
            'a.xlsx':zip_content('xl/workbook.xml'),
            'a.pptx':zip_content('ppt/presentation.xml'),
            'a.doc':b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'0'*20,
            'a.png':b'\x89PNG\r\n\x1a\n'+b'test',
            'a.jpeg':b'\xff\xd8\xff'+b'test',
            'russian.txt':'Привет мир'.encode('cp1251'),
            'a.csv':'ячейка;значение'.encode('utf-8'),
        }
        for name,blob in good.items():
            with self.subTest(name=name):
                mime,eligibility=files.inspect_blob(name,blob)
                self.assertTrue(mime)
                self.assertEqual(eligibility,'supported_next_stage' if Path(name).suffix in {'.pdf','.docx','.xlsx','.txt','.csv'} else 'deferred')

    def test_reject_mismatched_mime_and_traversal(self):
        for name,blob in [('../a.pdf',b'%PDF-1.4'),('C:\\fake.pdf',b'%PDF-1.4'),('shell.exe',b'abc'),
                          ('file.pdf',b'not pdf'),('file.png',b'not png'),('a.docx',zip_content('xl/workbook.xml')),
                          ('something.txt',b'abc\x00xyz'),('empty.txt',b'')]:
            with self.subTest(name=name,blob=blob[:6]):
                with self.assertRaises(files.FileProblem):files.inspect_blob(name,blob)

    def test_zip_bomb_traversal_and_symlink(self):
        bad=zip_content('word/document.xml',b'a'*(80*1024*1024+1))
        with self.assertRaises(files.FileProblem):files.inspect_blob('s.docx',bad)
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            z.writestr('word/document.xml',b'ok')
            z.writestr('../evil',b'evil')
        with self.assertRaises(files.FileProblem):files.inspect_blob('s.docx',b.getvalue())

    def test_base64_bounds_and_immutable_names(self):
        obj={'name':'x.pdf','data_base64':base64.b64encode(b'%PDF-1.4 test').decode()}
        self.assertEqual(files.decode_upload(obj)[1],b'%PDF-1.4 test')
        for mutate in [{'name':'x.pdf','data_base64':'bad=='},
                       {'name':'x.pdf','data_base64':'AA==','unknown':1},
                       {'name':'evil.pdf/../x.pdf','data_base64':obj['data_base64']}]:
            with self.assertRaises(files.FileProblem):files.decode_upload(mutate)

    def test_atomic_write_path_and_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            fid='f400fc19-629d-4d6e-a9cc-567891ba49df'
            blob=b'%PDF-1.7 test'
            h=hashlib.sha256(blob).hexdigest()
            ref=files._version_ref(fid,1,h)
            saved=files._write_durable(folder,ref,blob)
            self.assertEqual(saved.read_bytes(),blob)
            with self.assertRaises(files.FileProblem):files._write_durable(folder,ref,blob)
            with self.assertRaises(files.FileProblem):files._safe_path(folder,'research_library/../../outside')

    def test_commit_acknowledgement_uncertain_preserves_bytes(self):
        # Simulate a server that committed but dropped the acknowledgement.
        # Removing bytes here could leave a committed DB record pointing nowhere.
        class LostAck:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def commit(self):raise OSError('Lost COMMIT acknowledgement')
            def rollback(self):pass
        payload={'name':'test.pdf','data_base64':base64.b64encode(b'%PDF-1.4 test').decode()}
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            with patch.object(files,'_project',return_value={'id':'a'*32}), \
                 patch.object(files,'_project_folder',return_value=folder), \
                 patch.object(files,'_connect',return_value=LostAck()), \
                 patch.object(files,'_locked_project'), \
                 patch.object(files,'_quotas'), \
                 patch.object(files,'_create_source'), \
                 patch.object(files,'_save_version',return_value={}):
                with self.assertRaises(OSError):files.add_file('a'*32,payload)
            self.assertEqual(len(list(folder.rglob('*.bin'))),1)

    def test_repair_scan_finds_stale_incoming_tempfile(self):
        import time
        class FakeConn:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,*args):return self
            def fetchall(self):return []
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            incomplete=folder/'research_library'/'00'/'.incoming-crashed'
            incomplete.parent.mkdir(parents=True)
            incomplete.write_bytes(b'partial write')
            old=time.time()-900
            os.utime(incomplete,(old,old))
            with patch.object(files,'_project',return_value={'id':'a'*32}), \
                 patch.object(files,'_project_folder',return_value=folder), \
                 patch.object(files,'_connect',return_value=FakeConn()):
                scan=files.repair_scan('a'*32)
                self.assertIn('research_library/00/.incoming-crashed',scan['orphaned'])
                repaired=files.repair_scan('a'*32,quarantine=True)
                self.assertEqual(repaired['quarantined'],1)
            self.assertFalse(incomplete.exists())
            self.assertEqual(len(list((folder/'research_quarantine').iterdir())),1)

    @unittest.skipUnless(hasattr(os,'symlink'),'Symlinks unsupported')
    def test_never_follow_symlink_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'project';folder.mkdir()
            external=Path(temp)/'elsewhere';external.mkdir()
            try:(folder/'research_library').symlink_to(external,target_is_directory=True)
            except OSError:self.skipTest('Cannot create symlink')
            with self.assertRaises(files.FileProblem):files._safe_path(folder,'research_library/01/doc.bin')

if __name__=='__main__':unittest.main()
