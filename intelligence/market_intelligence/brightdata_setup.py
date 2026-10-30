"""One-time local Bright Data token setup for ContentMachine on Windows.

No command-line token argument, no GitHub/CI storage, no stdout secret.
Always reuses the same CM_BRIGHTDATA_API_TOKEN already consumed by the
Market Intelligence provider, its background jobs and read-only preflight.
Never enables paid collection without an explicit admin-side decision.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
import sys
import tempfile

from intelligence import config
from .brightdata_preflight import check

API_KEY = "CM_BRIGHTDATA_API_TOKEN"
ENABLED = "CM_BRIGHTDATA_METRICS_ENABLED"
RIGHTS = "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED"
KEY_RE = re.compile(r"[A-Za-z0-9._~+/=-]{16,1024}\\Z")


def _accepted(token: str) -> bool:
    return isinstance(token,str) and KEY_RE.fullmatch(token) is not None


def _write_local_env(path: Path, token: str) -> None:
    """Atomic key replacement, preserve existing local database settings."""
    if not _accepted(token):
        raise ValueError("Неверный формат ключа. Он не сохранён.")
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Символьная ссылка вместо .env запрещена.")
    if path.exists() and not path.is_file():
        raise ValueError("Путь .env не является файлом.")
    if path.exists() and path.stat().st_size>256_000:
        raise ValueError("Файл .env слишком большой.")
    existing=path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines=existing.splitlines()
    replacements={API_KEY:token, ENABLED:"0", RIGHTS:"0"}
    found=set()
    updated=[]
    for line in lines:
        raw=line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key=raw.split("=",1)[0].strip()
            if key in replacements:
                if key not in found:
                    updated.append(key+"="+replacements[key])
                    found.add(key)
                continue
        updated.append(line)
    for name,value in replacements.items():
        if name not in found:
            updated.append(name+"="+value)
    # Replacing secrets is a local operation: the generated file remains
    # ignored by Git, and source control will never receive these bytes.
    output="\\n".join(updated)+"\\n"
    fd,tmp=tempfile.mkstemp(prefix=".env-brightdata-",suffix=".tmp",dir=str(path.parent))
    try:
        try:
            os.fchmod(fd,0o600)
        except (AttributeError,OSError):
            pass
        with os.fdopen(fd,"w",encoding="utf-8",newline="\\n") as stream:
            stream.write(output)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install(*, input_secret=None, environment=None) -> dict:
    """Reuse previously configured token; otherwise ask only once locally."""
    env=config.local_env() if environment is None else environment
    existing=os.environ.get(API_KEY,"").strip() or env.get(API_KEY,"").strip()
    if _accepted(existing):
        return {"already_configured":True,"local_secret_present":True,
                "message":"Токен уже настроен. Повторно вводить не нужно."}
    read=input_secret or (lambda:getpass.getpass("Вставь Bright Data API-токен (ввод скрыт): "))
    token=read().strip()
    if not _accepted(token):
        raise ValueError("Неверный формат токена; ничего не записано.")
    _write_local_env(config.ENV_FILE,token)
    return {"already_configured":False,"local_secret_present":True,
            "message":"Токен сохранён только на этом ПК. Платный сбор пока выключен."}


def main() -> int:
    print("Bright Data — однократная локальная настройка")
    print("Токен не попадёт в Git, браузер и вывод терминала.")
    try:
        result=install()
    except (ValueError,OSError,EOFError,KeyboardInterrupt):
        print("Настройка не выполнена. Ключ не опубликован.")
        return 2
    print(result["message"])
    print("Проверяю авторизацию без платного сбора...")
    probe=check()
    print("Авторизация: "+probe.get("status","check_failed"))
    print("HTTP: "+str(probe.get("http_status")))
    print("Платных запросов: 0")
    if probe.get("status")!="authenticated":
        print("Токен сохранён для диагностики, но доступ ещё не подтверждён.")
        return 3
    print("Готово. Тот же токен будет использован во всех Bright Data задачах Market Intelligence.")
    print("Сбор метрик активируется отдельно после проверки тарифа и прав.")
    return 0


if __name__=="__main__":
    sys.exit(main())
