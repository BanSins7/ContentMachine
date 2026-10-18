#!/usr/bin/env python3
"""Supervise ContentMachine and fast-forward a private GitHub checkout.

No GitHub token is stored here. Authentication is handled by the user's Git
Credential Manager, established once while cloning the repository.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
REPO = "BanSins7/ContentMachine"
URL = "http://127.0.0.1:8765"
INTERVAL = 60
ACTIVE = {"queued", "processing"}


def log(message: str):
    print(f"[ContentMachine AutoUpdate] {message}", flush=True)


def git(*args: str, timeout: int = 50) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "Git failed").strip()[:350])
    return proc.stdout.strip()


def validate_remote(remote: str):
    """Reject invalid Python before replacing files or stopping the live server."""
    filenames = git("ls-tree", "-r", "--name-only", remote).splitlines()
    for name in filenames:
        if name.endswith(".py") and (name == "app.py" or name.startswith(("worker/", "tests/"))
                                  or name == "auto_update.py" or name.startswith("intelligence/")):
            source = git("show", f"{remote}:{name}")
            try:
                ast.parse(source, filename=name)
            except SyntaxError as exc:
                raise RuntimeError(f"GitHub update has invalid Python in {name}: {exc}") from exc


def sync_latest() -> bool:
    """Return True after fast-forwarding main; never discard local changes."""
    if not (ROOT / ".git").exists():
        raise RuntimeError("Нужен Git-клон проекта. Запусти установщик один раз.")
    if git("branch", "--show-current") != "main":
        raise RuntimeError("Автообновление работает только на ветке main.")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Есть локальные изменения в коде. Git ничего не перезаписал.")
    git("fetch", "--quiet", "origin", "main", timeout=60)
    here = git("rev-parse", "HEAD")
    remote = git("rev-parse", "refs/remotes/origin/main")
    if here == remote:
        return False
    git("merge-base", "--is-ancestor", here, remote)
    validate_remote(remote)
    git("merge", "--ff-only", "origin/main")
    log("Новая версия установлена из GitHub.")
    return True


def is_busy(state: dict) -> bool:
    for job in state.get("jobs", []):
        if job.get("status") in ACTIVE:
            return True
    for job in state.get("discovery", []):
        if job.get("status") in ACTIVE:
            return True
    return False


def get_state() -> dict | None:
    try:
        with urlopen(URL + "/api/state", timeout=3) as result:
            payload = json.load(result)
        return payload if payload.get("ok") else None
    except (URLError, OSError, TimeoutError, ValueError):
        return None


def migrate_local_settings():
    """Copy the previous private settings if present; never print key contents."""
    target = ROOT / "worker" / "openrouter_key.txt"
    usage = ROOT / "worker" / "openrouter_usage.json"
    if target.exists() and target.stat().st_size > 0:
        return
    downloads = Path.home() / "Downloads"
    candidates = []
    if not downloads.exists():
        return
    # Previous ZIPs were usually unpacked into named ContentMachine/ContentZavod folders.
    for folder in downloads.iterdir():
        if folder.is_dir() and ("contentmachine" in folder.name.lower() or
                                "contentzavod" in folder.name.lower() or
                                "content_zavod" in folder.name.lower()):
            candidates += list(folder.glob("worker/openrouter_key.txt"))
            candidates += list(folder.glob("*/worker/openrouter_key.txt"))
    candidates = [p for p in candidates if p.is_file()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for item in candidates:
        try:
            data = item.read_text(encoding="utf-8").strip()
            if data.startswith("sk-or-") and "\n" not in data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(data, encoding="utf-8")
                if os.name != "nt":
                    target.chmod(0o600)
                old_usage = item.with_name("openrouter_usage.json")
                if old_usage.exists() and not usage.exists():
                    shutil.copy2(old_usage, usage)
                log("Локальная настройка OpenRouter перенесена из старой папки.")
                return
        except (OSError, UnicodeError):
            continue
    log("Старый ключ не найден: при необходимости добавь его локально в worker/openrouter_key.txt.")


def ensure_dependencies():
    req = ROOT / "worker" / "requirements.txt"
    intelligence_req = ROOT / "intelligence" / "requirements.txt"
    runtime = ROOT / ".cm_runtime"
    runtime.mkdir(exist_ok=True)
    stamp = runtime / "requirements.sha256"
    digest = hashlib.sha256(req.read_bytes() + intelligence_req.read_bytes()).hexdigest()
    all_found = all(importlib.util.find_spec(name) is not None
                    for name in ("yt_dlp", "faster_whisper", "imageio_ffmpeg", "psycopg"))
    if all_found and (not stamp.exists() or stamp.read_text() == digest):
        stamp.write_text(digest, encoding="ascii")
        return
    log("Установка зависимостей Python…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req), "-r", str(intelligence_req)], check=True)
    stamp.write_text(digest, encoding="ascii")


def ensure_database_schema():
    """Apply pending SQL migrations when the local PostgreSQL connection is configured.

    No Docker or PostgreSQL is started automatically. Schema updates remain opt-in
    through the local .env created by SETUP_POSTGRES_WINDOWS.bat.
    """
    from intelligence import config
    if not config.enabled():
        return []
    if not config.dsn():
        raise RuntimeError("PostgreSQL enabled but CM_DB_DSN is absent.")
    import psycopg
    from intelligence.migrate import apply_migrations
    with psycopg.connect(config.dsn(), connect_timeout=5) as conn:
        return apply_migrations(conn)


def checkout_revision() -> str:
    return git("rev-parse", "HEAD")


def start_server(first: bool = False):
    env = dict(os.environ, CZ_LANGUAGE="ru", CZ_WHISPER_MODEL="small", CM_NO_BROWSER="1")
    # The running server reports the commit it was launched from. External Pull
    # cannot silently make the running code appear newer than it is.
    try:
        env["CM_SERVING_REVISION"] = checkout_revision()
    except (OSError, RuntimeError):
        env["CM_SERVING_REVISION"] = "unknown"
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "app.py")],
                            cwd=str(ROOT), env=env)
    if first:
        for _ in range(30):
            if proc.poll() is not None:
                raise RuntimeError("Локальный сервер не запустился. Порт 8765 может быть занят.")
            if get_state() is not None:
                webbrowser.open(URL)
                break
            time.sleep(.4)
    return proc


def restart_server(proc):
    """Stop a previously healthy server only when a validated update is ready."""
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return start_server()


def main():
    log("GitHub: " + REPO + " / main | проверка раз в минуту")
    migrate_local_settings()
    try:
        sync_latest()
    except Exception as exc:
        log("Обновление при запуске отложено: " + str(exc))
    try:
        ensure_dependencies()
    except Exception as exc:
        # Optional dependency downloads can time out. Do not make that take
        # down the entire website when the Python server still runs.
        log("Зависимости не полностью обновлены, пробую запустить сайт: " + str(exc))
    try:
        applied = ensure_database_schema()
        if applied:
            log("PostgreSQL migrations applied: " + ", ".join(applied))
    except Exception as exc:
        log("PostgreSQL schema is not ready (downloaders remain available): " + str(exc).splitlines()[0][:220])
    if get_state() is not None:
        raise RuntimeError("Порт 8765 уже занят другим сервером. Сначала закрой старую версию.")
    proc = start_server(first=True)
    served_revision = checkout_revision()
    last_warning = None
    try:
        while True:
            time.sleep(INTERVAL)
            # Crucial: even a dead server needs a GitHub fetch to self-heal.
            # Previously we skipped updates whenever /api/state did not work,
            # resulting in a permanent crash loop on a syntax error.
            if proc.poll() is not None:
                try:
                    log("Сервер завершился; проверяю GitHub на исправление…")
                    sync_latest()
                    try:
                        ensure_dependencies()
                        ensure_database_schema()
                    except Exception as dep_error:
                        log("Зависимости/БД: " + str(dep_error).splitlines()[0][:220])
                except Exception as exc:
                    log("Обновление при восстановлении: " + str(exc))
                proc = start_server()
                served_revision = checkout_revision()
                continue
            state = get_state()
            if state is None or is_busy(state):
                continue
            try:
                git("fetch", "--quiet", "origin", "main", timeout=60)
                current = git("rev-parse", "HEAD")
                remote = git("rev-parse", "refs/remotes/origin/main")
                if current != remote:
                    if git("status", "--porcelain", "--untracked-files=no"):
                        raise RuntimeError("Есть локальные изменения, обновление отложено.")
                    validate_remote(remote)
                    log("Доступна версия; проверяю зависимости, не останавливая работающий сайт…")
                    sync_latest()
                    try:
                        ensure_dependencies()
                        ensure_database_schema()
                    except Exception as exc:
                        # Leave the running server alive and undo the checkout
                        # (only tracked files; status was checked clean above).
                        git("reset", "--hard", current)
                        raise RuntimeError("Зависимости/миграции не готовы; сайт остался на прежней версии: " + str(exc))
                    proc = restart_server(proc)
                    time.sleep(1.3)
                    if proc.poll() is not None or get_state() is None:
                        # Runtime failures can slip past syntax checks. Roll back
                        # the new code and restore the last working server.
                        log("Новая версия не запустилась. Возвращаю предыдущую…")
                        if proc.poll() is None:
                            proc.terminate()
                            proc.wait(timeout=10)
                        git("reset", "--hard", current)
                        proc = start_server()
                        raise RuntimeError("Новая версия не прошла проверку запуска, откат выполнен.")
                    served_revision = checkout_revision()
                    log("Готово. Обнови вкладку браузера (F5).")
                elif current != served_revision:
                    # GitHub Desktop (or git pull) updated the very same checkout.
                    # Previous code only noticed updates fetched by this process,
                    # leaving the already-running Python server on an old version.
                    # Respect active downloads and never overwrite Desktop's files.
                    if git("branch", "--show-current") != "main":
                        raise RuntimeError("GitHub Desktop переключил ветку; нужен main.")
                    validate_remote(current)
                    log("GitHub Desktop обновил файлы: применяю зависимости и миграции…")
                    ensure_dependencies()
                    ensure_database_schema()
                    proc = restart_server(proc)
                    time.sleep(1.3)
                    if proc.poll() is not None or get_state() is None:
                        raise RuntimeError("Новая версия после Pull не запустилась. Проверь окно сервера; Git-файлы не изменены.")
                    served_revision = current
                    log("Обновление после Pull применено. Обнови вкладку браузера (Ctrl+F5).")
                last_warning = None
            except Exception as exc:
                msg = str(exc)
                if msg != last_warning:
                    log("Ошибка обновления (сайт продолжает работать): " + msg)
                    last_warning = msg
    except KeyboardInterrupt:
        log("Остановка сервиса.")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("Ошибка запуска: " + str(exc))
        sys.exit(1)