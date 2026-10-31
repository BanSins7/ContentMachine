"""Tesseract emits literal TSV, where OCR text can contain unmatched quotes.

Marketing-only copied GA12 parser regression; no external OCR/MP4 needed.
"""
from __future__ import annotations

import unittest

from intelligence.market_intelligence._copied.group_a.extractors.ocr import (
    OCRConfig, OCRError,
)
from intelligence.market_intelligence._copied.group_a.extractors.tesseract_ocr import (
    _parse_tsv,
)

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(text):
    return (HEADER + "\n" +
        "5\t1\t1\t1\t1\t1\t20\t30\t40\t15\t93.5\t" +
        text + "\n").encode("utf-8")


def parse(raw):
    return _parse_tsv(
        raw, cfg=OCRConfig(), rotation=0,
        source_width=100, source_height=100,
    )


class MarketTesseractLiteralTSVTests(unittest.TestCase):
    def test_unmatched_opening_quote_is_recognized_text_not_csv_syntax(self):
        rows = parse(tsv('"Hello'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], '"Hello')
        self.assertEqual(rows[0]["confidence_pct"], "93.50")

    def test_balanced_quotes_remain_literal_evidence(self):
        rows = parse(tsv('"face yoga"'))
        self.assertEqual(rows[0]["text"], '"face yoga"')

    def test_unmatched_cyrillic_quote_is_not_discarded(self):
        rows = parse(tsv('«Привет"'))
        self.assertEqual(rows[0]["text"], '«Привет"')

    def test_corrupted_numeric_column_still_fails_closed(self):
        raw=tsv('screen').replace(b'\t40\t15\t',b'\tbad\t15\t')
        with self.assertRaises(OCRError) as e:
            parse(raw)
        self.assertEqual(e.exception.code, "ocr_tsv_invalid")

    def test_invalid_utf8_still_fails_closed(self):
        with self.assertRaises(OCRError) as e:
            parse(tsv("word") + b"\xff")
        self.assertEqual(e.exception.code, "ocr_tsv_invalid")

    def test_missing_columns_still_fails_closed(self):
        with self.assertRaises(OCRError) as e:
            parse((HEADER+"\n5\t1\n").encode("utf-8"))
        self.assertEqual(e.exception.code, "ocr_tsv_invalid")


if __name__ == "__main__":
    unittest.main()
