"""Read-only Bright Data connectivity and token diagnostics.

Uses ONLY an authenticated GET for an intentionally absent snapshot ID.
No paid POST, no real accounts and no secret values in results/logs.
This checks reachability and identifies obvious auth/network failures.
A 404 does not verify data-collection rights or valid billing access.
"""
from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request

from intelligence.market_intelligence import brightdata_provider as bd

TEST_SNAPSHOT = "s_0000000000000000"
TEST_URL = bd.ENDPOINT + "/datasets/v3/progress/" + TEST_SNAPSHOT

_DIAGNOSES = {
    "key_missing": "Сначала сохрани API-ключ Bright Data.",
    "dns": "DNS не может найти api.brightdata.com. Проверь DNS и доступ к домену.",
    "timeout": "Bright Data не ответил вовремя. Проверь соединение, VPN и прокси.",
    "tls": "Ошибка проверки HTTPS-сертификата. Проверь системное время и сертификаты Windows.",
    "proxy": "Прокси-сервер не разрешил соединение. Проверь его настройки.",
    "connection": "Не удалось установить HTTPS-соединение с api.brightdata.com.",
    "unauthorized": "Bright Data отклонил API-ключ (401). Перепроверь ключ в настройках.",
    "forbidden": "Bright Data отклонил доступ (403). Проверь разрешения аккаунта и dataset.",
    "api_error": "Bright Data доступен, но ответил ошибкой API.",
    "reachable": "HTTPS-соединение с Bright Data работает. GET ответил; права на сбор ещё не проверены.",
}


def _result(code: str, *, http_status: int | None = None) -> dict:
    return {
        "ok": code == "reachable",
        "code": code,
        "message": _DIAGNOSES[code],
        "http_status": http_status,
        "paid": False,
        "key_verified": False,
    }


def _network_error(error: BaseException) -> dict:
    reason = getattr(error, "reason", error)
    if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return _result("tls")
    if isinstance(reason, ssl.SSLError):
        return _result("tls")
    if isinstance(reason, socket.gaierror):
        return _result("dns")
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _result("timeout")
    # Proxy failures from urllib may be nested as OSError; don't echo raw
    # exception text because proxy URLs can contain credentials.
    if isinstance(reason, OSError):
        number = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
        if number in (11001, 11002, -2, -3):
            return _result("dns")
        if number in (10060, 110, 60):
            return _result("timeout")
    return _result("connection")


def probe_brightdata(*, opener=None, token_reader=None) -> dict:
    """Safe, on-demand, non-billable authentication route connectivity probe.

    401/403 are actionable. 404 for a deliberately nonexistent snapshot proves
    connectivity but does NOT prove a valid token: auth check ordering varies.
    """
    if token_reader is None:
        token_reader = bd._read_only_token
    if opener is None:
        opener = urllib.request.urlopen
    try:
        token = token_reader()
    except (ValueError, RuntimeError, OSError):
        return _result("key_missing")
    if not isinstance(token, str) or not token:
        return _result("key_missing")
    request = urllib.request.Request(
        TEST_URL,
        method="GET",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "ContentMachine-InstagramTop100-Diagnostics/1",
        },
    )
    try:
        with opener(request, timeout=12) as response:
            status = response.status
        if status in (401, 403):
            return _result("unauthorized" if status == 401 else "forbidden", http_status=status)
        if 200 <= status < 500:
            return _result("reachable", http_status=status)
        return _result("api_error", http_status=status)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return _result("unauthorized", http_status=401)
        if error.code == 403:
            return _result("forbidden", http_status=403)
        if error.code == 407:
            return _result("proxy", http_status=407)
        if error.code in (400, 404, 405, 410, 422):
            return _result("reachable", http_status=error.code)
        return _result("api_error", http_status=error.code)
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
        return _network_error(error)
