"""Import only user-supplied text. No server file paths or remote URL fetching."""
from __future__ import annotations
import csv
import io
from .normalize import validate_candidate

class ImportErrorPublic(ValueError):
    pass

MAX_ROWS = 10000
MAX_BYTES = 2_000_000

def parse_content(text: str, fmt: str) -> list[dict]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportErrorPublic("Импорт превышает 2 МБ.")
    if fmt == "urls":
        rows = [{"url": s.strip()} for s in text.splitlines() if s.strip()]
    elif fmt == "csv":
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if not reader.fieldnames or "url" not in reader.fieldnames or len(reader.fieldnames) > 20:
                raise ImportErrorPublic("CSV должен содержать колонку url.")
            allowed = {"url","platform","title","published_at","duration_seconds"}
            if not set(reader.fieldnames).issubset(allowed):
                raise ImportErrorPublic("Неподдерживаемые CSV-колонки.")
            rows = []
            for line in reader:
                if None in line or any(v is None for v in line.values()):
                    raise ImportErrorPublic("Некорректная строка CSV.")
                # Keep raw duration text for row-level validation. Never serialize
                # float("NaN") or Infinity into a PostgreSQL JSONB job payload.
                rows.append(line)
        except csv.Error as exc:
            raise ImportErrorPublic("Некорректный формат CSV.") from exc
    else:
        raise ImportErrorPublic("Формат должен быть urls или csv.")
    if not 1 <= len(rows) <= MAX_ROWS:
        raise ImportErrorPublic("Допустимо от 1 до 10 000 строк.")
    return rows

def validate_rows(rows: list[dict]):
    return [validate_candidate(row) for row in rows]
