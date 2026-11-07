"""Windows-user-encrypted credentials for ContentMachine and Carousel Studio.

No secret is returned to the browser, logs, provider result archives or Git.
Windows DPAPI CurrentUser protects the local on-disk blob; no plaintext fallback.
Environment/.env settings continue working when no Studio override is saved.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1] / "marketing-qa" / "carousel-studio"
FILE = ROOT / "studio-credentials.dpapi"
_LOCK = threading.RLock()
NAMES = ("openrouter_api_key", "rusgpt_api_key", "brightdata_api_token")
PROVIDERS = ("openrouter", "rusgpt")
MAX_SECRET_LEN = 4096

class CredentialSettingsError(ValueError):
    """An allowlisted error that never contains submitted key values."""



class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(payload: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Сохранение ключей в Studio поддерживается только в Windows.")
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(payload)
    in_blob = _Blob(len(payload), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _Blob()
    if protect:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
                             ctypes.POINTER(_Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(_Blob)]
    else:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                             ctypes.POINTER(_Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    try:
        if protect:
            ok = function(ctypes.byref(in_blob), "ContentMachine Carousel Studio",
                          None, None, None, 0x01, ctypes.byref(out_blob))
        else:
            ok = function(ctypes.byref(in_blob), None, None, None, None,
                          0x01, ctypes.byref(out_blob))
        if not ok:
            raise OSError("Windows DPAPI operation failed.")
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _load() -> dict:
    if not FILE.exists():
        return {}
    if FILE.is_symlink() or not FILE.is_file():
        raise CredentialSettingsError("Небезопасный путь хранилища ключей.")
    raw = FILE.read_bytes()
    if not raw or len(raw) > 50000:
        raise CredentialSettingsError("Хранилище ключей повреждено; старый файл не изменён.")
    try:
        value = json.loads(_dpapi(raw, protect=False).decode("utf-8"))
    except (UnicodeError, ValueError, OSError) as exc:
        raise CredentialSettingsError("Не удаётся расшифровать прежние настройки Windows. Старый файл не изменён.") from None
    if type(value) is not dict or set(value) - {*NAMES, "brightdata_authorized", "preferred_provider"}:
        raise CredentialSettingsError("Формат локального хранилища не поддерживается; старый файл не изменён.")
    return value


def saved() -> dict:
    with _LOCK:
        return _load()


def token(service: str) -> str:
    if service not in NAMES:
        raise ValueError("Неизвестный провайдер.")
    return saved().get(service, "")


def authorized() -> bool:
    return saved().get("brightdata_authorized") is True


def provider() -> str:
    selected = saved().get("preferred_provider", "openrouter")
    if selected not in PROVIDERS:
        raise ValueError("Неизвестный провайдер в настройках Studio.")
    return selected



def store(changes: dict) -> dict:
    """Blank input keeps old key. An explicit consent checkbox controls rights."""
    if type(changes) is not dict or set(changes) - {*NAMES, "brightdata_authorized", "preferred_provider"}:
        raise CredentialSettingsError("Неизвестные поля настроек.")
    if not changes:
        raise CredentialSettingsError("Нет изменений настроек.")
    clean = dict(changes)
    labels = {"openrouter_api_key":"OpenRouter",
              "rusgpt_api_key":"Rus-GPT",
              "brightdata_api_token":"Bright Data"}
    for key in NAMES:
        value = clean.get(key, "")
        label = labels[key]
        if type(value) is not str:
            raise CredentialSettingsError(f"{label}: ключ должен быть текстом.")
        # Copy/paste may include a trailing newline or external whitespace.
        # Strip ONLY at the boundaries. Never alter internal key bytes.
        value = value.strip()
        if len(value) > MAX_SECRET_LEN:
            raise CredentialSettingsError(f"{label}: ключ слишком длинный.")
        if value and any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
            raise CredentialSettingsError(
                f"{label}: ключ содержит пробелы или невидимые символы внутри.")
        clean[key] = value
    if "brightdata_authorized" in clean and type(clean["brightdata_authorized"]) is not bool:
        raise CredentialSettingsError("Подтверждение разрешений должно быть да/нет.")
    if "preferred_provider" in clean and (
        type(clean["preferred_provider"]) is not str
        or clean["preferred_provider"] not in PROVIDERS
    ):
        raise CredentialSettingsError("Недопустимый провайдер: выберите OpenRouter или Rus-GPT.")
    with _LOCK:
        values = _load()
        for key in NAMES:
            if clean.get(key):
                values[key] = clean[key]
        if "brightdata_authorized" in clean:
            values["brightdata_authorized"] = clean["brightdata_authorized"]
        if "preferred_provider" in clean:
            values["preferred_provider"] = clean["preferred_provider"]
        if not values:
            return {"saved": False}
        encrypted = _dpapi(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
                           protect=True)
        ROOT.mkdir(parents=True, exist_ok=True)
        if FILE.is_symlink():
            raise CredentialSettingsError("Небезопасный путь хранилища ключей.")
        temp = FILE.with_suffix(".pending")
        try:
            with temp.open("wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, FILE)
        finally:
            if temp.exists():
                temp.unlink()
    return {"saved": True}


def status() -> dict:
    # Status contains booleans only, never token strings, prefixes or suffixes.
    current = saved()
    return {
        "preferred_provider": current.get("preferred_provider", "openrouter"),
        "openrouter_saved": bool(current.get("openrouter_api_key")),
        "rusgpt_saved": bool(current.get("rusgpt_api_key")),
        "brightdata_saved": bool(current.get("brightdata_api_token")),
        "brightdata_authorized": current.get("brightdata_authorized") is True,
        "storage": "Windows DPAPI (текущий пользователь), только локально",
    }
