"""Offline deterministic document extraction; only exact observed bytes become segments.

PSI stage 04. No model, URL fetch, formula execution or cloud/OCR service.
The caller is responsible for checking immutable source bytes and isolating
this parser inside a bounded worker process.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

PARSER_VERSION = 'document-extractor/1.0.0'
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 100
MAX_ROWS = 2000
MAX_COLS = 80
MAX_SEGMENTS = 1800
MAX_TABLES = 100
MAX_CHARS_PER_SEGMENT = 12000
MAX_OUTPUT_BYTES = 3 * 1024 * 1024
SUPPORTED = {'.pdf', '.docx', '.xlsx', '.csv', '.txt', '.md'}


class ParseFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def configuration(raw=None):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {'ocr'} or type(raw.get('ocr', False)) is not bool:
        raise ParseFailure('invalid_config', 'Допустим только параметр ocr=true/false.')
    return {'ocr': raw.get('ocr', False)}


def config_hash(config):
    return hashlib.sha256(json.dumps(configuration(config), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class Collector:
    def __init__(self, fmt):
        self.fmt = fmt
        self.segments = []
        self.tables = []
        self.warnings = []
        self.partial = False
        self.total_chars = 0

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)
        self.partial = True

    def add(self, text, locator, role='text', **more):
        if not isinstance(text, str) or not text.strip():
            return
        # Segments are splits of actual extracted strings; offsets are measured
        # against the explicitly named original block/paragraph/cell value.
        for begin in range(0, len(text), MAX_CHARS_PER_SEGMENT):
            if len(self.segments) >= MAX_SEGMENTS:
                self.warn('segment_limit')
                return
            part = text[begin:begin + MAX_CHARS_PER_SEGMENT]
            if not part.strip():
                continue
            loc = {**locator, 'start_char': begin, 'end_char': begin + len(part)}
            row = {'order': len(self.segments), 'role': role, 'text': part,
                   'locator': loc, **more}
            if len(canonical(row)) > 90000:
                self.warn('segment_too_large')
                continue
            self.segments.append(row)
            self.total_chars += len(part)

    def table(self, cells, locator):
        if len(self.tables) >= MAX_TABLES:
            self.warn('table_limit')
            return
        self.tables.append({'locator': locator, 'rows': cells})

    def finish(self, *, coverage=None, engine=None):
        state = 'partial' if self.partial else 'completed'
        if not self.segments and not self.tables:
            state = 'unavailable'
            if not self.warnings:
                self.warnings.append('no_extractable_text')
        result = {
            'schema_version': 'document-segments/v1',
            'parser_version': PARSER_VERSION,
            'format': self.fmt,
            'status': state,
            'segments': self.segments,
            'tables': self.tables,
            'coverage': coverage or {},
            'warnings': self.warnings,
            'extraction_engine': engine or self.fmt + '/native',
            'is_llm_output': False,
        }
        # Never return a falsely complete truncated result.
        if len(canonical(result)) > MAX_OUTPUT_BYTES:
            result['status'] = 'partial'
            result['warnings'].append('output_byte_limit')
            # Remove batches to avoid quadratic JSON serialisation on long files.
            while len(canonical(result)) > MAX_OUTPUT_BYTES and result['segments']:
                del result['segments'][max(0, len(result['segments'])-100):]
            while len(canonical(result)) > MAX_OUTPUT_BYTES and result['tables']:
                del result['tables'][max(0,len(result['tables'])-10):]
            if len(canonical(result)) > MAX_OUTPUT_BYTES:
                raise ParseFailure('output_limit', 'Результат не укладывается в безопасный лимит.')
        return result


def text_decode(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    elif data.startswith(b'\xef\xbb\xbf'):
        encoding = 'utf-8-sig'
    else:
        encoding = 'utf-8'
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError:
        try:
            return data.decode('cp1251'), 'cp1251'
        except UnicodeDecodeError as exc:
            raise ParseFailure('invalid_encoding', 'Текстовый файл не удалось декодировать без потерь.') from exc


def parse_text(data, ext, out):
    text, encoding = text_decode(data)
    if '\x00' in text:
        raise ParseFailure('binary_text', 'В документе найдены двоичные байты.')
    pos = 0
    lines = text.splitlines(keepends=True)
    for lineno, line in enumerate(lines, 1):
        out.add(line, {'kind': 'text', 'line_start': lineno, 'line_end': lineno,
                       'file_char_offset': pos}, 'markdown' if ext == '.md' else 'line')
        pos += len(line)
    return {'lines': len(lines), 'encoding': encoding}


def parse_csv(data, out):
    text, encoding = text_decode(data)
    if '\x00' in text:
        raise ParseFailure('binary_text', 'CSV содержит двоичные данные.')
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ';' if sample.count(';') > sample.count(',') else ','
    csv.field_size_limit(1024 * 1024)
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    count = 0
    try:
        for count, cells in enumerate(reader, 1):
            if count > MAX_ROWS:
                out.warn('row_limit'); break
            if len(cells) > MAX_COLS:
                out.warn('column_limit')
            for col, value in enumerate(cells[:MAX_COLS], 1):
                if value:
                    out.add(value, {'kind': 'spreadsheet', 'sheet': 'CSV',
                                    'cell': column_label(col) + str(count),
                                    'row': count, 'column': col}, 'cell')
            if cells and len(out.tables) < MAX_TABLES:
                out.table([cells[:MAX_COLS]], {'kind': 'spreadsheet', 'sheet': 'CSV',
                             'row_start': count, 'row_end': count})
    except (csv.Error, UnicodeError) as exc:
        raise ParseFailure('invalid_csv', 'Некорректный CSV.') from exc
    return {'rows': min(count, MAX_ROWS), 'encoding': encoding, 'delimiter': delimiter}


def column_label(n):
    letters = ''
    while n:
        n, mod = divmod(n-1, 26)
        letters = chr(65+mod) + letters
    return letters


def parse_xlsx(data, out):
    import openpyxl
        # Never evaluate external links, macros or formulae. Cached value is read
    # independently from formula cells, and may be genuinely unavailable.
    try:
        formulas = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        cached = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise ParseFailure('invalid_spreadsheet', 'Не удалось прочитать структуру XLSX.') from exc
    try:
        if len(formulas.sheetnames) > 32:
            out.warn('sheet_limit')
        for name in formulas.sheetnames[:32]:
            sheet = formulas[name]
            values = cached[name]
            if sheet.max_row and sheet.max_row > MAX_ROWS:
                out.warn('row_limit')
            if sheet.max_column and sheet.max_column > MAX_COLS:
                out.warn('column_limit')
            for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row or 0, MAX_ROWS),
                                       max_col=min(sheet.max_column or 0, MAX_COLS)):
                cells = []
                for cell in row:
                    if cell.value is None:
                        cells.append(None);continue
                    addr = cell.coordinate
                    value = cell.value
                    meta = {}
                    if cell.data_type == 'f':
                        cached_value = values[addr].value
                        meta = {'formula': str(value), 'formula_executed': False,
                                'cached_value_available': cached_value is not None}
                        value = cached_value if cached_value is not None else 'неизвестно'
                    if isinstance(value, (bool, int, float)):
                        value = str(value)
                    elif value is not None:
                        value = str(value)
                    if value is not None:
                        out.add(value, {'kind':'spreadsheet','sheet':name,'cell':addr,
                                        'row':cell.row,'column':cell.column}, 'cell', **meta)
                    cells.append(value)
                if any(v is not None for v in cells) and len(out.tables) < MAX_TABLES:
                    out.table([cells], {'kind':'spreadsheet','sheet':name,
                                        'row_start':row[0].row,'row_end':row[0].row})
    finally:
        formulas.close();cached.close()
    return {'sheets': min(len(formulas.sheetnames), 32), 'sheet_names': formulas.sheetnames[:32]}


def parse_docx(data, out):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseFailure('invalid_docx', 'Не удалось прочитать DOCX.') from exc
    para_index = 0
    table_index = 0
    section = ''
    order = document.iter_inner_content() if hasattr(document, 'iter_inner_content') else []
    for element in order:
        if isinstance(element, Paragraph):
            para_index += 1
            if para_index > 5000:
                out.warn('paragraph_limit');break
            style = element.style.name if element.style is not None else ''
            if style.startswith(('Heading', 'Заголовок')):
                section = element.text[:250]
            out.add(element.text, {'kind':'docx','paragraph':para_index,
                                   'section':section,'style':style}, 'heading' if style.startswith(('Heading','Заголовок')) else 'list_item' if style.startswith('List') else 'paragraph')
        elif isinstance(element, Table):
            table_index += 1
            if table_index > MAX_TABLES:
                out.warn('table_limit');break
            rows = []
            for r_idx, row in enumerate(element.rows[:MAX_ROWS], 1):
                values = []
                for c_idx, cell in enumerate(row.cells[:MAX_COLS], 1):
                    value = cell.text
                    values.append(value)
                    if value:
                        out.add(value, {'kind':'docx','cell':f'{r_idx},{c_idx}',
                                        'table':table_index,'row':r_idx,'column':c_idx,
                                        'section':section}, 'table_cell')
                rows.append(values)
            out.table(rows, {'kind':'docx','table':table_index,'section':section})
    return {'paragraphs': para_index, 'tables': table_index}


def parse_pdf(data, out, *, ocr=False):
    import fitz
    try:
        doc = fitz.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise ParseFailure('invalid_pdf', 'PDF повреждён или не читается.') from exc
    scanned = []
    ocr_pages = []
    page_count = len(doc)
    try:
        if doc.needs_pass:
            raise ParseFailure('encrypted_pdf', 'PDF зашифрован.')
        if len(doc) > MAX_PAGES:
            out.warn('page_limit')
        for n in range(min(len(doc), MAX_PAGES)):
            page = doc[n]
            blocks = page.get_text('blocks', sort=True)
            meaningful = 0
            for block in blocks:
                if len(block) < 7 or block[6] != 0:
                    continue
                text = block[4]
                meaningful += len(text.strip())
                loc = {'kind':'pdf','page':n+1,'block':int(block[5]),
                       'bbox': [round(float(x),2) for x in block[:4]]}
                out.add(text, loc, 'page_text')
            if len(out.tables) < MAX_TABLES:
                try:
                    found = page.find_tables()
                    for j, table in enumerate(found.tables):
                        out.table(table.extract(), {'kind':'pdf','page':n+1,
                                                   'table':j,'bbox':[round(float(x),2) for x in table.bbox]})
                except (AttributeError, ValueError, RuntimeError):
                    out.warn('pdf_table_detection_incomplete')
            if meaningful < 15:
                scanned.append(n+1)
                if ocr:
                    try:
                        import pytesseract
                        from PIL import Image
                        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                        image = Image.frombytes('RGB', (pix.width,pix.height),pix.samples)
                        recognized = pytesseract.image_to_string(image, lang='rus+eng', config='--psm 3', timeout=12)
                        if recognized.strip():
                            out.add(recognized, {'kind':'pdf','page':n+1,'block':0,
                                                 'ocr':True}, 'ocr_text',
                                    ocr_engine='tesseract/local', confidence=None,
                                    confidence_status='not_measured')
                            ocr_pages.append(n+1)
                    except (ImportError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
                        out.warn('ocr_unavailable')
        if scanned and len(ocr_pages) < len(scanned):
            out.warn('some_pages_need_ocr')
    finally:
        doc.close()
    return {'pages': min(page_count, MAX_PAGES), 'scanned_or_empty_pages': scanned,
            'ocr_pages':ocr_pages, 'ocr_requested':ocr,
            'ocr_coverage': {'attempted_pages':len(scanned) if ocr else 0,
                             'recognized_pages':len(ocr_pages),
                             'confidence': None, 'confidence_status': 'not_measured'},
            'reading_order': 'text_blocks_sorted_by_position_approximate'}


def extract(data: bytes, filename: str, *, config=None):
    cfg = configuration(config)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ParseFailure('invalid_size', 'Файл не проходит лимиты размера.')
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        return {'schema_version':'document-segments/v1','parser_version':PARSER_VERSION,
                'format':ext,'status':'unavailable','segments':[],'tables':[],
                'coverage':{},'warnings':['unsupported_format'],'extraction_engine':'none',
                'is_llm_output':False}
    collector = Collector(ext)
    try:
        if ext == '.pdf':
            coverage = parse_pdf(data, collector, ocr=cfg['ocr'])
        elif ext == '.docx':
            coverage = parse_docx(data, collector)
        elif ext == '.xlsx':
            coverage = parse_xlsx(data, collector)
        elif ext == '.csv':
            coverage = parse_csv(data, collector)
        else:
            coverage = parse_text(data, ext, collector)
    except ParseFailure:
        raise
    except (OSError, ValueError, RuntimeError, MemoryError) as exc:
        raise ParseFailure('parse_failed', 'Ошибка извлечения документа.') from exc
    result = collector.finish(coverage=coverage)
    result['config'] = cfg
    return result
