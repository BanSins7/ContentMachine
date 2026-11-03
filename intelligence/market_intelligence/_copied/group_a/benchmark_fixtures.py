"""GA22 rights-clear, locally GENERATED fixture media and SHA-256 manifest.

Nothing is downloaded; no copyrighted videos/voices, paid models, QDP, cloud.
Every binary is created fresh by the host's recorded FFmpeg and Pillow builds.
Hashes are measured after generation, NOT claimed identical across codecs/OS.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .benchmark_metrics import BenchmarkError

SCHEMA="ga22-synthetic-gold/v1"
GENERATOR="ga22-fixture-generator/v1"


def _sha(path):
    dig=sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            dig.update(chunk)
    return dig.hexdigest()


def _tool_version(executable):
    try:
        proc=subprocess.run(
            [executable,"-version"],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            timeout=10,check=True
        )
    except (OSError,subprocess.SubprocessError) as exc:
        raise BenchmarkError("media_tool_unavailable") from exc
    return proc.stdout.decode("utf-8",errors="replace").splitlines()[0][:200]


def _ffmpeg(executable,args):
    try:
        completed=subprocess.run(
            [executable,"-hide_banner","-loglevel","error","-nostdin",
             "-threads","1","-y",*args],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,timeout=60,check=False
        )
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise BenchmarkError("synthetic_media_generation_failed") from exc
    if completed.returncode!=0:
        raise BenchmarkError("synthetic_media_generation_failed")


def validate_gold_definition(path):
    source=Path(path)
    if not source.is_file() or source.is_symlink():
        raise BenchmarkError("gold_definition_missing")
    try:
        gold=json.loads(source.read_text(encoding="utf-8"))
    except (ValueError,UnicodeError) as exc:
        raise BenchmarkError("gold_definition_invalid") from exc
    if type(gold) is not dict or set(gold)!={
        "schema_version","generator_version","rights","gold_provenance",
        "speech_policy","cases"
    } or gold["schema_version"]!=SCHEMA or gold["generator_version"]!=GENERATOR:
        raise BenchmarkError("gold_contract_version_mismatch")
    if type(gold["cases"]) is not list or not 5<=len(gold["cases"])<=24:
        raise BenchmarkError("gold_case_count_invalid")
    ids=set()
    for item in gold["cases"]:
        if type(item) is not dict or set(item)!={"id","kind","expected","tags"} | (
            {"path"} if "path" in item else {"paths"}
        ):
            raise BenchmarkError("gold_case_fields_invalid")
        if (type(item["id"]) is not str or
            item["id"] in ids or len(item["id"])>64 or
            not item["id"].replace("_","").isascii() or
            not item["id"].replace("_","").isalnum()):
            raise BenchmarkError("gold_case_id_invalid")
        ids.add(item["id"])
        files=item["paths"] if "paths" in item else [item["path"]]
        if (type(files) is not list or not files or
            any(type(f) is not str or f not in (
                "en_image.png","ru_image.png","cut_video.mp4",
                "mute_video.mp4","noise_video.mp4","dynamic_caption_video.mp4"
            ) for f in files)):
            raise BenchmarkError("gold_case_assets_invalid")
        kind=item["kind"]
        if kind not in ("image","video","photo_carousel","mixed_carousel"):
            raise BenchmarkError("gold_content_kind_invalid")
        if ((kind in ("image","video") and len(files)!=1) or
            (kind=="photo_carousel" and
             (len(files)<2 or any(not x.endswith(".png") for x in files))) or
            (kind=="mixed_carousel" and
             (len(files)<2 or not any(x.endswith(".png") for x in files)
              or not any(x.endswith(".mp4") for x in files)))):
            raise BenchmarkError("gold_kind_asset_mismatch")
        if type(item["tags"]) is not list or any(type(x) is not str for x in item["tags"]):
            raise BenchmarkError("gold_case_tags_invalid")
        expected=item["expected"]
        if type(expected) is not dict or set(expected)!={
            "ocr_lines","subtitle_presence","scene_boundaries_sec",
            "audio_events","asr_reference"
        }:
            raise BenchmarkError("gold_expected_fields_invalid")
        if (type(expected["ocr_lines"]) is not list or
            any(type(x) is not str for x in expected["ocr_lines"]) or
            expected["subtitle_presence"] not in ("true","false","unknown") or
            type(expected["scene_boundaries_sec"]) is not list or
            type(expected["audio_events"]) is not list or
            expected["asr_reference"] is not None):
            raise BenchmarkError("gold_synthetic_truth_invalid")
    return gold


def generate_synthetic_corpus(*,gold_path,output_dir,ffmpeg="ffmpeg"):
    """Write media and manifest only into an EMPTY caller-owned directory.

    No post-hoc fabricated WER, human licenses, GPU claims or model predictions.
    """
    import PIL
    from PIL import Image,ImageDraw,ImageFont
    root=Path(output_dir)
    if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
        raise BenchmarkError("corpus_output_must_be_empty_dir")
    gold=validate_gold_definition(gold_path)
    binary=shutil.which(ffmpeg)
    if not binary:
        raise BenchmarkError("ffmpeg_missing")
    ff_version=_tool_version(binary)
    try:
        font=ImageFont.truetype("DejaVuSans.ttf",30)
    except OSError as exc:
        raise BenchmarkError("font_with_russian_glyphs_missing") from exc
    font_path=Path(getattr(font,"path",""))
    if not font_path.is_file():
        raise BenchmarkError("font_provenance_unavailable")
    font_sha=_sha(font_path)

    for name,text,color in (
        ("en_image.png","ORIGINAL ENGLISH TEXT",(22,45,102)),
        ("ru_image.png","ПРОВЕРКА РУССКОГО ТЕКСТА",(16,84,67)),
    ):
        image=Image.new("RGB",(640,360),color)
        painter=ImageDraw.Draw(image)
        painter.rectangle((22,104,618,250),fill=(252,252,252))
        painter.text((34,158),text,font=font,fill=(12,12,25))
        image.save(root/name,format="PNG",optimize=False)

    # Exact scene reference is at synthetic source splice 2.000 sec; after
    # codec processing the measured video PTS may differ by one frame.
    _ffmpeg(binary,[
        "-f","lavfi","-i","color=c=red:s=320x180:r=10:d=2",
        "-f","lavfi","-i","color=c=blue:s=320x180:r=10:d=2",
        "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map","[v]","-c:v","mpeg4","-q:v","5",
        "-pix_fmt","yuv420p",str(root/"cut_video.mp4"),
    ])
    _ffmpeg(binary,[
        "-f","lavfi","-i","color=c=black:s=320x180:r=10:d=4",
        "-map","0:v","-c:v","mpeg4","-q:v","5",
        "-pix_fmt","yuv420p",str(root/"mute_video.mp4"),
    ])
    _ffmpeg(binary,[
        "-f","lavfi","-i","color=c=gray:s=320x180:r=10:d=4",
        "-f","lavfi","-i","anoisesrc=color=white:sample_rate=16000:duration=4",
        "-map","0:v","-map","1:a",
        "-c:v","mpeg4","-q:v","5","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","64k","-shortest",str(root/"noise_video.mp4"),
    ])
    _ffmpeg(binary,[
        "-loop","1","-framerate","10","-t","2","-i",str(root/"en_image.png"),
        "-loop","1","-framerate","10","-t","2","-i",str(root/"ru_image.png"),
        "-filter_complex","[0:v]format=yuv420p[v0];[1:v]format=yuv420p[v1];"
                          "[v0][v1]concat=n=2:v=1:a=0[v]",
        "-map","[v]","-c:v","mpeg4","-q:v","5","-pix_fmt","yuv420p",
        str(root/"dynamic_caption_video.mp4"),
    ])

    records=[]
    for case in gold["cases"]:
        files=case["paths"] if "paths" in case else [case["path"]]
        assets=[]
        for name in files:
            path=root/name
            if path.is_symlink() or not path.is_file():
                raise BenchmarkError("generated_fixture_missing")
            assets.append({
                "name":name,"sha256":_sha(path),"byte_size":path.stat().st_size
            })
        records.append({
            "id":case["id"],"kind":case["kind"],
            "tags":case["tags"],"assets":assets,
            "expected":case["expected"],
        })
    manifest={
        "schema_version":"ga22-generated-corpus/v1",
        "generation":"local_generated_synthetic_no_external_media",
        "source_gold_sha256":_sha(Path(gold_path)),
        "generator_version":GENERATOR,
        "rights":gold["rights"],
        "gold_provenance":gold["gold_provenance"],
        "speech_policy":gold["speech_policy"],
        "environment":{
            "python":sys.version.split()[0],
            "ffmpeg":ff_version,"pillow":PIL.__version__,
            "font_sha256":font_sha,
            "device":"cpu_synthetic_generation_only",
        },
        "cases":records,
    }
    (root/"manifest.json").write_text(
        json.dumps(manifest,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8"
    )
    return manifest


def verify_generated_manifest(root,manifest,gold_path):
    root=Path(root)
    if type(manifest) is not dict or manifest.get("schema_version")!="ga22-generated-corpus/v1":
        raise BenchmarkError("generated_manifest_invalid")
    if manifest.get("source_gold_sha256")!=_sha(Path(gold_path)):
        raise BenchmarkError("gold_file_changed")
    gold=validate_gold_definition(gold_path)
    if len(manifest.get("cases",[]))!=len(gold["cases"]):
        raise BenchmarkError("corpus_case_count_changed")
    allowed={"en_image.png","ru_image.png","cut_video.mp4","mute_video.mp4",
             "noise_video.mp4","dynamic_caption_video.mp4"}
    for actual,expected in zip(manifest["cases"],gold["cases"]):
        if actual["id"]!=expected["id"] or actual["expected"]!=expected["expected"]:
            raise BenchmarkError("corpus_gold_changed")
        expected_files=(
            expected["paths"] if "paths" in expected else [expected["path"]]
        )
        if [asset.get("name") for asset in actual["assets"]] != expected_files:
            raise BenchmarkError("corpus_slide_order_or_asset_missing")
        for asset in actual["assets"]:
            if type(asset) is not dict or set(asset)!={"name","sha256","byte_size"}:
                raise BenchmarkError("corpus_asset_shape_invalid")
            name=asset["name"]
            if (type(asset["sha256"]) is not str or
                len(asset["sha256"])!=64 or
                any(c not in "0123456789abcdef" for c in asset["sha256"]) or
                type(asset["byte_size"]) is not int or asset["byte_size"]<1):
                raise BenchmarkError("corpus_asset_proof_invalid")
            if name not in allowed:
                raise BenchmarkError("corpus_path_not_allowed")
            path=root/name
            if path.is_symlink() or not path.is_file() or _sha(path)!=asset["sha256"] or (
                path.stat().st_size!=asset["byte_size"]
            ):
                raise BenchmarkError("corpus_asset_sha_mismatch")
    return True
