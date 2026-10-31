"""One-time, explicit Windows setup of SHA-pinned LOCAL Marketing Group A models.

Does not touch jobs, project DB or remote AI providers. The only downloads
are requested model artifacts from a fixed official Silero Git revision and
a revision-pinned public Systran model on Hugging Face.
Never executed automatically at video-analysis time.

Usage:
  python -m scripts.setup_market_group_a_models --ocr
  python -m scripts.setup_market_group_a_models --vad
  python -m scripts.setup_market_group_a_models --asr
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from intelligence.market_intelligence._copied.group_a.extractors.local_asr import _model_inventory
from intelligence.market_intelligence._copied.group_a.extractors.tesseract_ocr import tesseract_fingerprint
from intelligence.market_intelligence.market_ocr_compat import build_tesseract_adapter

MODEL_ROOT = ROOT / "marketing-qa" / "local-models"
ENV_FILE = ROOT / ".env"
SILERO_REVISION = "dbacf536adadf42210f37ae50fbaf75f6235b3cf"
SILERO_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    + SILERO_REVISION + "/src/silero_vad/data/silero_vad.jit"
)
WHISPER_REPO = "Systran/faster-whisper-small"


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def store_settings(changes: dict[str, str]):
    """Preserve existing .env secrets/settings; replace only explicitly provisioned keys."""
    original = ENV_FILE.read_text(encoding="utf-8-sig") if ENV_FILE.exists() else ""
    lines = original.splitlines()
    updated = set()
    for index, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name = line.partition("=")[0].strip()
        if name in changes:
            lines[index] = name + "=" + changes[name]
            updated.add(name)
    for name, value in changes.items():
        if name not in updated:
            lines.append(name + "=" + value)
    output = "\n".join(lines).rstrip("\n") + "\n"
    temp = ENV_FILE.with_name(".env.marketing-models.tmp")
    try:
        temp.write_text(output, encoding="utf-8")
        os.replace(temp, ENV_FILE)
    finally:
        if temp.exists():
            temp.unlink()
    print("[CONFIGURED] " + ", ".join(sorted(changes)))


def setup_ocr():
    candidates = [
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe"),
    ]
    binary = next((Path(p).resolve() for p in candidates if p and Path(p).is_file()), None)
    if binary is None:
        raise RuntimeError(
            "Tesseract 5 not installed. Run: winget install -e --id "
            "UB-Mannheim.TesseractOCR; then rerun --ocr")
    data = binary.parent / "tessdata"
    actual, languages = tesseract_fingerprint(str(binary), str(data))
    build_tesseract_adapter(binary=str(binary), tessdata_dir=str(data),
                     expected_sha256=actual)
    print("[OCR] Tesseract verified; languages:", ", ".join(sorted(languages)))
    if "rus" not in languages:
        print("[NOTE] rus.traineddata is missing. Install the official "
              "Russian tessdata pack to recognize Russian text.")
    store_settings({
        "CM_MARKET_TESSERACT_PIN": actual,
        "CM_MARKET_TESSERACT_BINARY": str(binary),
        "CM_MARKET_TESSERACT_DATA": str(data),
    })


def setup_vad():
    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("PyTorch is missing: python -m pip install torch") from exc
    from intelligence.market_intelligence._copied.group_a.extractors.vad import LocalSileroJIT
    target = MODEL_ROOT / "silero_vad.jit"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        part = target.with_suffix(".jit.part")
        try:
            with urlopen(SILERO_URL, timeout=60) as response, part.open("wb") as dst:
                size = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > 100 * 1024 * 1024:
                        raise RuntimeError("Silero model exceeds approved size")
                    dst.write(block)
            if not 0 < size <= 100 * 1024 * 1024:
                raise RuntimeError("Silero JIT empty or oversized")
            os.replace(part, target)
        finally:
            if part.exists():
                part.unlink()
    sha = digest_file(target)
    version = "silero-vad/" + SILERO_REVISION[:12]
    model = LocalSileroJIT(str(target.resolve()),
                           expected_sha256=sha, version=version, device="cpu")
    # Checks JIT execution, sample rate and shape rather than just file existence.
    probabilities = model.probabilities(b"\x00" * 1024)
    if len(probabilities) != 1:
        raise RuntimeError("Silero inference returned unexpected window count")
    store_settings({
        "CM_MARKET_VAD_ENABLED": "1",
        "CM_MARKET_VAD_MODEL_PATH": str(target.resolve()),
        "CM_MARKET_VAD_MODEL_SHA256": sha,
        "CM_MARKET_VAD_MODEL_VERSION": version,
    })
    print("[VAD] Official revision-pinned Silero JIT loaded and tested.")


def setup_asr():
    try:
        from huggingface_hub import HfApi, snapshot_download
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "ASR dependencies missing: python -m pip install "
            "faster-whisper huggingface-hub") from exc

    revision = HfApi().model_info(WHISPER_REPO).sha
    if not isinstance(revision, str) or len(revision) != 40 or not all(
        c in "0123456789abcdef" for c in revision
    ):
        raise RuntimeError("Hugging Face did not return an immutable commit SHA")
    cache = MODEL_ROOT / "hf-cache"
    downloaded = Path(snapshot_download(
        repo_id=WHISPER_REPO, revision=revision, cache_dir=str(cache),
        allow_patterns=[
            "config.json", "model.bin", "tokenizer.json",
            "preprocessor_config.json", "vocabulary.*",
        ],
    ))
    target = MODEL_ROOT / ("faster-whisper-small-" + revision[:12])
    target.mkdir(parents=True, exist_ok=True)
    for name in ("model.bin", "config.json", "tokenizer.json",
                 "preprocessor_config.json", "vocabulary.txt", "vocabulary.json"):
        source = downloaded / name
        if source.is_file():
            shutil.copyfile(source, target / name)
    digest = _model_inventory(target.resolve())
    # Verify this exact local directory can be opened without network.
    WhisperModel(str(target.resolve()), device="cpu", compute_type="int8",
                 local_files_only=True, num_workers=1, cpu_threads=2)
    engine_version = importlib.metadata.version("faster-whisper")
    store_settings({
        "CM_MARKET_ASR_ENABLED": "1",
        "CM_MARKET_ASR_MODEL_DIR": str(target.resolve()),
        "CM_MARKET_ASR_MODEL_SHA256": digest,
        "CM_MARKET_ASR_MODEL_VERSION": "systran-small-" + revision[:12],
        "CM_MARKET_ASR_ENGINE_VERSION": engine_version,
    })
    print("[ASR] Systran multilingual small model pinned to", revision[:12],
          "with verified local model inventory.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", action="store_true", help="Pin preinstalled Tesseract")
    parser.add_argument("--vad", action="store_true", help="Download and pin Silero JIT")
    parser.add_argument("--asr", action="store_true", help="Download and pin multilingual faster-whisper")
    args = parser.parse_args()
    if not (args.ocr or args.vad or args.asr):
        parser.error("Choose at least one of --ocr, --vad, --asr")
    if args.ocr:
        setup_ocr()
    if args.vad:
        setup_vad()
    if args.asr:
        setup_asr()
    print("[OK] Local-only provisioning complete. Restart app.py to reload .env.")


if __name__ == "__main__":
    main()
