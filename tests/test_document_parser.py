"""Stage 04 offline fixtures: no network/AI/DB required."""
import io
import json
import unittest
import hashlib

from intelligence.project_sources.document_parser import (extract, ParseFailure, config_hash, canonical,
    PARSER_VERSION)


class DocumentExtractionUnit(unittest.TestCase):
    def test_russian_txt_utf8_cp1251_line_char_offsets(self):
        for encoding in ('utf-8','cp1251'):
            data='Привет, мир!\nТестовая строка\n'.encode(encoding)
            result=extract(data,'readme.txt')
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['coverage']['lines'],2)
            self.assertEqual(result['segments'][0]['locator']['file_char_offset'],0)
            self.assertEqual(result['segments'][1]['locator']['file_char_offset'],len('Привет, мир!\n'))
            self.assertEqual(''.join(s['text'] for s in result['segments']), 'Привет, мир!\nТестовая строка\n')
            self.assertEqual(canonical(result),canonical(extract(data,'readme.txt')))

    def test_md_multiline_and_empty_text(self):
        result=extract('# Заголовок\n\nТекст'.encode(),'readme.md')
        self.assertEqual(result['segments'][0]['locator']['line_start'],1)
        self.assertEqual(result['status'],'completed')
        empty=extract(b'\n\n','empty.md')
        self.assertEqual(empty['status'],'unavailable')

    def test_csv_preserves_cells_and_cyrillic(self):
        result=extract('Название;Цена\nЯблоко;42\n'.encode('cp1251'),'data.csv')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['coverage']['delimiter'],';')
        self.assertTrue(any(s['locator']['cell']=='A2' and s['text']=='Яблоко' for s in result['segments']))
        self.assertTrue(any(t['locator']['row_start']==2 for t in result['tables']))

    def test_xlsx_formula_not_executed(self):
        from openpyxl import Workbook
        b=io.BytesIO();w=Workbook();s=w.active;s.title='Продажи';s['A1']='Доход';s['A2']=23;s['B2']='=A2*4';w.save(b)
        result=extract(b.getvalue(),'report.xlsx')
        self.assertEqual(result['status'],'completed')
        s=next(s for s in result['segments'] if s['locator']['cell']=='B2')
        self.assertEqual(s['text'],'неизвестно')
        self.assertEqual(s['formula'],'=A2*4')
        self.assertFalse(s['formula_executed'])
        self.assertEqual(result['coverage']['sheet_names'],['Продажи'])

    def test_docx_headings_paragraphs_tables_in_order(self):
        from docx import Document
        doc=Document();doc.add_heading('Исследование',level=1);doc.add_paragraph('Рынок растёт.')
        t=doc.add_table(rows=2,cols=2);t.cell(0,0).text='Показатель';t.cell(0,1).text='Сумма';t.cell(1,0).text='Продажи';t.cell(1,1).text='100'
        doc.add_paragraph('Вывод')
        b=io.BytesIO();doc.save(b)
        result=extract(b.getvalue(),'report.docx')
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['tables'])
        self.assertEqual(result['tables'][0]['rows'][1][0],'Продажи')
        self.assertEqual(result['segments'][0]['text'],'Исследование')
        self.assertTrue(any(x['locator'].get('paragraph')==3 for x in result['segments']))
        self.assertTrue(any(x['locator'].get('table')==1 for x in result['segments']))

    def test_pdf_multpage_empty_and_scanned_honesty(self):
        import fitz
        d=fitz.open();p=d.new_page();p.insert_text((72,72),'Page one with some words')
        d.new_page();p=d.new_page();p.insert_text((72,72),'Page three with some words')
        raw=d.tobytes();d.close()
        result=extract(raw,'report.pdf')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['coverage']['scanned_or_empty_pages'],[2])
        self.assertTrue(any(s['locator']['page']==3 for s in result['segments']))
        self.assertEqual(result['coverage']['ocr_pages'],[])
        self.assertIn('some_pages_need_ocr',result['warnings'])
        self.assertEqual(canonical(result),canonical(extract(raw,'report.pdf')))

    def test_encrypted_and_corrupt(self):
        with self.assertRaises(ParseFailure):extract(b'%PDF-damaged','bad.pdf')
        with self.assertRaises(ParseFailure):extract(b'bad zip','bad.docx')
        with self.assertRaises(ParseFailure):extract(b'bad zip','bad.xlsx')

    def test_long_segments_bounded_and_truncated_marked(self):
        text='Длинный текст абвгде!\n'*1000
        result=extract(text.encode(),'huge.txt')
        self.assertTrue(all(len(s['text'])<=12000 for s in result['segments']))
        self.assertEqual(result['status'],'completed')
        self.assertEqual(''.join(s['text'] for s in result['segments']),text)

    def test_duplicate_content_and_config_digest(self):
        self.assertEqual(config_hash({'ocr':False}),config_hash({}))
        self.assertNotEqual(config_hash({'ocr':False}),config_hash({'ocr':True}))
        with self.assertRaises(ParseFailure):extract(b'1','r.txt',config={'ocr':'no'})
        self.assertEqual(extract(b'123','.doc')['status'],'unavailable')

if __name__=='__main__':unittest.main()
