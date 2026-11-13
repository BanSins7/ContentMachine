"""Safe, atomic, reproducible ZIP copies of verified Instagram MP4 files.

Each completed batch contains 100 MP4 files; a final short batch is allowed.
Originals remain in InstagramArchive. No media is re-downloaded for packaging.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import zipfile
from pathlib import Path

from .core import CODE, username

BATCH_SIZE = 100
ZIP_NAME = re.compile(r"batch_([0-9]{3,})\.zip\Z")
MAX_ORIGINAL_BYTES = 2_000_000_000


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source(root: Path, entry: dict) -> tuple[Path, dict]:
    """Derive all source paths from validated IDs, never from provider URLs."""
    account = username(entry["username"])
    code = str(entry["shortcode"])
    if not CODE.fullmatch(code):
        raise ValueError("Неверный shortcode в задании архива.")
    folder = root / account
    video = folder / (code + ".mp4")
    metadata = folder / (code + ".json")
    if not video.is_file() or not metadata.is_file():
        raise ValueError(f"MP4/metadata отсутствует: @{account}/{code}")
    if video.is_symlink() or metadata.is_symlink():
        raise ValueError("Символьные ссылки запрещены при упаковке.")
    if not video.resolve().is_relative_to(root.resolve()):
        raise ValueError("Недопустимый путь исходного MP4.")
    size = video.stat().st_size
    if size < 1 or size > MAX_ORIGINAL_BYTES:
        raise ValueError("Размер исходного MP4 вне лимита.")
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    if (meta.get("verified_shortcode") != code
        or meta.get("source_url") != entry["url"]
        or not isinstance(meta.get("video_sha256"), str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", meta["video_sha256"])):
        raise ValueError(f"Не подтверждено происхождение: @{account}/{code}")
    digest = hashlib.sha256()
    with video.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != meta["video_sha256"].lower():
        raise ValueError(f"SHA-256 MP4 не совпадает: @{account}/{code}")
    return video, {
        "username": account,
        "shortcode": code,
        "url": entry["url"],
        "views": entry.get("views"),
        "published_at": entry.get("published_at"),
        "file": f"{account}/{code}.mp4",
        "byte_size": size,
        "sha256": digest.hexdigest(),
    }


def archive_path(base: Path, run_id: str, batch_number: int) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id) or type(batch_number) is not int or batch_number < 1:
        raise ValueError("Некорректный идентификатор ZIP.")
    return base / "archives" / run_id / f"batch_{batch_number:03d}.zip"


def _existing_matches(path: Path, names: list[str], signature: str) -> bool:
    """Fast integrity check without decompressing multi-gigabyte videos."""
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if zf.namelist() != names + ["manifest.json"]:
                return False
            manifest = json.loads(zf.read("manifest.json"))
            return (manifest.get("signature") == signature
                    and len(manifest.get("videos", [])) == len(names))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return False


def package_batch(base: Path, run_id: str, batch_number: int, entries: list[dict],
                  *, source_root: Path | None = None) -> dict:
    """Create an independent batch ZIP via temp file + atomic replace.

    MP4 is already compressed; ZIP_STORED avoids pointless re-encoding and
    preserves the original bytes. ZIP64 supports large batches.
    """
    if not entries or len(entries) > BATCH_SIZE:
        raise ValueError("В одном ZIP допускается от 1 до 100 видео.")
    root = source_root or Path.home() / "Downloads" / "InstagramArchive"
    dest = archive_path(base, run_id, batch_number)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # The signature includes all metadata. Already-built ZIPs continue to work
    # when original MP4s are moved/deleted: an archive is a separate copy.
    signature = hashlib.sha256(_json_bytes(entries)).hexdigest()
    names = []
    for entry in entries:
        account = username(entry["username"])
        code = str(entry["shortcode"])
        if not CODE.fullmatch(code):
            raise ValueError("Некорректный shortcode.")
        name = f"{account}/{code}.mp4"
        if name in names:
            raise ValueError("Повтор MP4 внутри одного батча.")
        names.append(name)

    if _existing_matches(dest, names, signature):
        return {"number": batch_number, "name": dest.name,
                "count": len(entries), "bytes": dest.stat().st_size,
                "status": "ready", "signature": signature}

    validated = [_source(root, entry) for entry in entries]
    manifest = {
        "schema_version": "instagram-reels-zip-batch/v1",
        "run_id": run_id, "batch_number": batch_number,
        "video_count": len(entries), "signature": signature,
        "videos": [metadata for _, metadata in validated],
    }
    tmp = dest.with_name(dest.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive:
            for video, metadata in validated:
                archive.write(video, arcname=metadata["file"])
            archive.writestr("manifest.json", _json_bytes(manifest))
        if not _existing_matches(tmp, names, signature):
            raise ValueError("ZIP не прошёл проверку структуры.")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"number": batch_number, "name": dest.name,
            "count": len(entries), "bytes": dest.stat().st_size,
            "status": "ready", "signature": signature}


def inventory_from_run(run: dict) -> list[dict]:
    """Preserve first-successful-download order across explicit resume calls."""
    order = run.setdefault("archive_inventory", [])
    seen = {(x["username"], x["shortcode"]) for x in order}
    for account in run["accounts"]:
        for post in account["posts"]:
            if post["status"] not in ("downloaded", "cached"):
                continue
            key = (account["username"], post["shortcode"])
            if key not in seen:
                order.append({
                    "username": account["username"], "shortcode": post["shortcode"],
                    "url": post["url"], "views": post["views"],
                    "published_at": post["published_at"],
                })
                seen.add(key)
    return order


def append_download(run: dict, account: str, post: dict) -> None:
    """Add one completed MP4 once, before the worker persists the post status."""
    if post["status"] not in ("cached", "downloaded"):
        return
    order = run.setdefault("archive_inventory", [])
    key = (username(account), str(post["shortcode"]))
    if any((item["username"], item["shortcode"]) == key for item in order):
        return
    order.append({
        "username": key[0], "shortcode": key[1],
        "url": post["url"], "views": post["views"],
        "published_at": post["published_at"],
    })


def package_available(run: dict, base: Path, *, include_partial: bool,
                      on_archive=None, source_root: Path | None = None) -> list[dict]:
    """Prepare full batches as they appear; finalizing also writes partial tail.

    on_archive callback lets caller durably record the result of each batch.
    """
    # _execute restores inventory once from persisted post states. Do not
    # rescan every account after every new download (quadratic at scale).
    inventory = run.setdefault("archive_inventory", [])
    full, remainder = divmod(len(inventory), BATCH_SIZE)
    count = full + (1 if include_partial and remainder else 0)
    existing = {x["number"]: x for x in run.setdefault("archives", [])}
    for index in range(1, count + 1):
        entries = inventory[(index-1)*BATCH_SIZE:index*BATCH_SIZE]
        result = package_batch(base, run["id"], index, entries, source_root=source_root)
        existing[index] = result
        run["archives"] = [existing[j] for j in sorted(existing)
                           if j <= count or (not include_partial and j == full + 1)]
        if on_archive is not None:
            on_archive(run)
    # A partial batch previously written at end of a cancelled run may remain
    # available until replaced by a larger tail on explicit resume.
    return run["archives"]
