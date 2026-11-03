"""Read-only local Bright Data authentication and dataset visibility probe.

Run: python -m intelligence.market_intelligence.brightdata_preflight
GET /datasets/list is documented by Bright Data. NO provider POST, collection,
charges, browser credentials, or database writes occur here.
The API key is loaded from the ignored local .env / server environment.
Never display the token, response body, or exception containing request data.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from intelligence import config
from .brightdata_provider import DATASETS, dataset_id

URL = "https://api.brightdata.com/datasets/list"
MAX_RESPONSE_BYTES = 8_000_000


class ProbeFailure(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_list(token: str, *, opener=None) -> tuple[int, object]:
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ProbeFailure("bad_local_token")
    request = urllib.request.Request(
        URL, method="GET",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "User-Agent": "ContentMachine-MarketPreflight/1"},
    )
    client = opener or urllib.request.build_opener(_NoRedirect())
    try:
        with client.open(request, timeout=25) as response:
            status = int(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProbeFailure("network_unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProbeFailure("oversized_dataset_catalog")
    if status != 200:
        return status, None
    try:
        return status, json.loads(raw)
    except (UnicodeError, ValueError):
        raise ProbeFailure("invalid_catalog_json") from None


def _is_video_dataset(platform: str, name: str | None) -> bool:
    """A catalog match must describe video posts, not comments or profiles."""
    if not isinstance(name, str):
        return False
    label = name.casefold()
    if platform == "tiktok":
        return "tiktok" in label and "posts" in label and not any(
            bad in label for bad in ("comment", "profile", "internal", "test")
        )
    if platform == "instagram":
        return "instagram" in label and "reels" in label
    if platform == "youtube":
        return "youtube" in label and "video" in label and "post" in label
    return False


def check(*, fetcher=_fetch_list) -> dict:
    """No side effects; a 200 validates auth, not a paid scrape entitlement."""
    token = config.setting("CM_BRIGHTDATA_API_TOKEN", "").strip()
    if not token:
        return {"status": "not_configured", "http_status": None,
                "message": "Добавь CM_BRIGHTDATA_API_TOKEN в локальный .env."}
    try:
        http_status, content = fetcher(token)
        if http_status != 200:
            return {
                "status": "unauthorized" if http_status == 401 else "http_error",
                "http_status": http_status,
                "message": "API отклонил проверку ключа."
                if http_status in (401, 403)
                else "Не удалось прочитать каталог Bright Data.",
            }
        if type(content) is not list or len(content) > 10000:
            raise ProbeFailure("unexpected_catalog_schema")
        indexed = {}
        matches = []
        for item in content:
            if type(item) is not dict:
                continue
            ident, name = item.get("id"), item.get("name")
            if type(ident) is not str or type(name) is not str:
                continue
            if len(ident) > 100 or len(name) > 250:
                continue
            indexed[ident] = name
            if any(word in name.lower() for word in ("tiktok","instagram","youtube")):
                matches.append({"id": ident, "name": name})
        return {
            "status": "authenticated", "http_status": 200,
            "catalog_entries": len(content),
            "market_datasets": matches[:45],
            "targets": {
                platform: {
                    "id": dataset_id(platform),
                    "listed": dataset_id(platform) in indexed,
                    "name": indexed.get(dataset_id(platform)),
                    "video_dataset": _is_video_dataset(
                        platform, indexed.get(dataset_id(platform))
                    ),
                } for platform in ("tiktok","instagram","youtube")
            },
            "collection_started": False,
            "scope": "read_only_catalog_not_scraper_permission",
        }
    except ProbeFailure as exc:
        return {"status": "check_failed", "http_status": None,
                "reason_code": str(exc), "collection_started": False}


def main() -> int:
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "authenticated" else 2


if __name__ == "__main__":
    sys.exit(main())
