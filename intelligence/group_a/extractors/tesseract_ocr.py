"""GA-MOD-12 Tesseract 5.x local, pinned traineddata + binary, TSV geometry.

Tesseract is CPU-only; OCR models must already be installed, with a
host-expected SHA256 inventory. No auto-download, shell, network, subprocess
without timeouts, or acceptance of unknown language claims.
Rotations are explicit; boxes are mapped back into ORIGINAL GA07 PNG pixels.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import csv
import json
import os
import re
import subprocess
import sys
import tempfile

from ..contracts_v1 import canonical_sha256
from .ocr import OCRConfig, OCRError, _text, _number
from .frames import MAX_PNG_BYTES, MAX_EDGE

MAX_TSV_BYTES=3*1024*1024
MAX_TESSDATA=240*1024*1024
MAX_BINARY=80*1024*1024
MAX_LANGS=24
VERSION_RE=re.compile(r"^tesseract\s+(5\.[0-9]+(?:\.[0-9]+)?(?:[-+][0-9A-Za-z._-]+)?)",re.I)
SHA_RE=re.compile(r"^[a-f0-9]{64}$")


def _sha_file(path,limit):
    if path.is_symlink() or not path.is_file() or not 1<=path.stat().st_size<=limit:
        raise OCRError("ocr_untrusted_model_file")
    digest=sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):
                digest.update(chunk)
    except OSError as exc:
        raise OCRError("ocr_model_unreadable") from exc
    return digest.hexdigest()


def tesseract_fingerprint(binary,tessdata_dir):
    """Full installed packs & exact executable fingerprint, no guessed model."""
    if (type(binary) is not str or type(tessdata_dir) is not str
        or not Path(binary).is_absolute() or not Path(tessdata_dir).is_absolute()):
        raise OCRError("ocr_model_path_invalid")
    exe=Path(binary);data=Path(tessdata_dir)
    if data.is_symlink() or not data.is_dir():
        raise OCRError("ocr_tessdata_unavailable")
    exehash=_sha_file(exe,MAX_BINARY)
    trained={}
    for candidate in sorted(data.glob("*.traineddata")):
        if len(trained)>=MAX_LANGS:
            raise OCRError("ocr_tessdata_budget_exceeded")
        lang=candidate.stem
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,30}",lang):
            raise OCRError("ocr_tessdata_invalid_language")
        trained[lang]=_sha_file(candidate,MAX_TESSDATA)
    if not trained:
        raise OCRError("ocr_tessdata_unavailable")
    return canonical_sha256({"binary_sha256":exehash,"traineddata":trained}),frozenset(trained)


def _run(runner,cmd,*,timeout,env):
    try:
        result=runner(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE,check=False,timeout=timeout,env=env)
    except subprocess.TimeoutExpired as exc:
        raise OCRError("ocr_engine_timeout") from exc
    except OSError as exc:
        raise OCRError("ocr_engine_unavailable") from exc
    if (type(result.returncode) is not int or type(result.stdout) is not bytes
        or type(result.stderr) is not bytes or
        len(result.stdout)>MAX_TSV_BYTES or len(result.stderr)>200_000):
        raise OCRError("ocr_process_output_limit")
    if result.returncode!=0:
        raise OCRError("ocr_engine_failed")
    return result.stdout,result.stderr


def _image(png,cfg,rotation):
    try:
        from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
        with Image.open(BytesIO(png)) as original:
            if (original.format!="PNG" or original.width<1 or original.height<1
                or original.width>MAX_EDGE or original.height>MAX_EDGE):
                raise OCRError("ocr_bad_png")
            original.load()
            source_size=original.size
            rotated=original.convert("RGB").rotate(rotation,expand=True)
            if cfg.preprocess=="autocontrast":
                rotated=ImageOps.autocontrast(rotated.convert("L"))
            elif cfg.preprocess=="sharpen":
                rotated=rotated.convert("L").filter(
                    ImageFilter.UnsharpMask(radius=1,percent=130,threshold=2))
            if cfg.scale==2:
                rotated=rotated.resize((rotated.width*2,rotated.height*2),
                                      Image.Resampling.LANCZOS)
            with BytesIO() as out:
                rotated.save(out,format="PNG")
                data=out.getvalue()
        if len(data)>MAX_PNG_BYTES*3:
            raise OCRError("ocr_preprocessing_output_limit")
        return data,source_size
    except OCRError:
        raise
    except Exception as exc:
        raise OCRError("ocr_bad_png") from exc


def _orig_bbox(left,top,width,height,scale,rotation,source_width,source_height):
    # Preserve original pixel extent with conservative edge rounding.
    x0=max(0,left//scale)
    y0=max(0,top//scale)
    x1=(left+width+scale-1)//scale
    y1=(top+height+scale-1)//scale
    if rotation==0:
        a,b,c,d=x0,y0,x1,y1
    elif rotation==90:
        a,b,c,d=source_width-y1,x0,source_width-y0,x1
    elif rotation==180:
        a,b,c,d=source_width-x1,source_height-y1,source_width-x0,source_height-y0
    elif rotation==270:
        a,b,c,d=y0,source_height-x1,y1,source_height-x0
    else:
        raise OCRError("ocr_rotation_invalid")
    if not (0<=a<c<=source_width and 0<=b<d<=source_height):
        raise OCRError("ocr_box_outside_frame")
    return {"x":a,"y":b,"width":c-a,"height":d-b}


def _parse_tsv(data,*,cfg,rotation,source_width,source_height):
    try:
        text=data.decode("utf-8-sig","strict")
        reader=csv.DictReader(StringIO(text),delimiter="\t",strict=True)
        required={"level","block_num","par_num","line_num","left",
                  "top","width","height","conf","text"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise OCRError("ocr_tsv_invalid")
        output=[];line_ids={}
        seen=set()
        for row in reader:
            if len(seen)>12000:
                raise OCRError("ocr_tsv_budget_exceeded")
            if type(row) is not dict or None in row:
                raise OCRError("ocr_tsv_invalid")
            if row["level"]!="5":
                continue
            phrase=row.get("text","").strip()
            if not phrase:
                continue
            confidence=Decimal(row["conf"])
            if not confidence.is_finite() or confidence<0:
                # Tess uses -1 on structural rows, but an observed text
                # must not be decorated with a fabricated confidence.
                raise OCRError("ocr_tsv_confidence_unavailable")
            confidence=_number(confidence)
            x,y,w,h=(int(row[k]) for k in ("left","top","width","height"))
            if x<0 or y<0 or w<=0 or h<=0:
                raise OCRError("ocr_tsv_box_invalid")
            group=tuple(int(row[k]) for k in ("block_num","par_num","line_num"))
            if min(group)<0:
                raise OCRError("ocr_tsv_line_invalid")
            if group not in line_ids:
                line_ids[group]=f"l{len(line_ids)+1:06d}"
            box=_orig_bbox(x,y,w,h,cfg.scale,rotation,source_width,source_height)
            token=_text(phrase)
            signature=(line_ids[group],token,*box.values())
            if signature in seen:
                continue
            seen.add(signature)
            output.append({"text":token,"confidence_pct":confidence,
                           "bbox":box,"line_ref":line_ids[group]})
            if len(output)>2000:
                raise OCRError("ocr_words_budget_exceeded")
        return output
    except OCRError:
        raise
    except (UnicodeError,ValueError,TypeError,OverflowError,csv.Error) as exc:
        raise OCRError("ocr_tsv_invalid") from exc


class TesseractAdapter:
    """Preinstalled Tesseract5, pinned CLI+every traineddata, no GPU fallback.

    The host can compute the model inventory with tesseract_fingerprint()
    while provisioning, then persist expected_sha256 independently.
    Changing any installed language pack or binary fails closed.
    """
    def __init__(self,*,binary,tessdata_dir,expected_sha256,runner=subprocess.run):
        if type(expected_sha256) is not str or not SHA_RE.fullmatch(expected_sha256):
            raise OCRError("ocr_model_pin_missing")
        if not callable(runner):
            raise OCRError("ocr_invalid_runner")
        self.binary=str(binary)
        self.tessdata_dir=str(tessdata_dir)
        self.runner=runner
        actual,self._installed=tesseract_fingerprint(self.binary,self.tessdata_dir)
        if actual!=expected_sha256:
            raise OCRError("ocr_model_mismatch")
        self._sha=expected_sha256
        env={**os.environ,"OMP_THREAD_LIMIT":"1","OMP_NUM_THREADS":"1"}
        out,err=_run(runner,[self.binary,"--version"],timeout=10,env=env)
        combined=(out+b"\n"+err).decode("utf-8","replace").strip()
        first=combined.splitlines()[0] if combined else ""
        match=VERSION_RE.match(first)
        if match is None:
            raise OCRError("ocr_version_unsupported")
        self._version=match.group(1)

    @property
    def identity(self):
        return {"engine":"tesseract","engine_version":self._version,
                "model_sha256":self._sha,"device":"cpu"}

    def process(self,png,config:OCRConfig):
        if not isinstance(config,OCRConfig):
            raise OCRError("invalid_ocr_config")
        if type(png) is not bytes or not 0<len(png)<=MAX_PNG_BYTES:
            raise OCRError("ocr_bad_png")
        current,langs=tesseract_fingerprint(self.binary,self.tessdata_dir)
        if current!=self._sha or langs!=self._installed:
            raise OCRError("ocr_model_mismatch")
        used=[x for x in config.languages if x in langs]
        missing=set(config.languages)-set(used)
        if missing:
            if (config.missing_language!="fallback_eng" or "eng" not in used):
                raise OCRError("ocr_language_unavailable")
            used=["eng"]
        if not used:
            raise OCRError("ocr_language_unavailable")
        best=None
        for index,rotation in enumerate(config.rotations):
            data,(width,height)=_image(png,config,rotation)
            with tempfile.TemporaryDirectory(prefix="ga-ocr-") as temp:
                path=Path(temp)/"frame.png"
                path.write_bytes(data)
                env={**os.environ,"OMP_THREAD_LIMIT":str(config.cpu_threads),
                     "OMP_NUM_THREADS":str(config.cpu_threads)}
                cmd=[self.binary,str(path),"stdout","--tessdata-dir",
                     self.tessdata_dir,"-l","+".join(used),"--psm",
                     str(config.psm),"tsv"]
                output,_=_run(self.runner,cmd,timeout=config.timeout_sec,env=env)
            words=_parse_tsv(output,cfg=config,rotation=rotation,
                             source_width=width,source_height=height)
            # Prefer the best sum of non-calibrated confidence*characters.
            # Rotation is an inference for the *single frame* only.
            score=sum((Decimal(w["confidence_pct"])*len(w["text"])
                       for w in words),Decimal(0))
            rank=(score,len(words),-index)
            if best is None or rank>best[0]:
                best=(rank,rotation,words)
        words=best[2]
        return {
            "state":"partial_language" if missing else (
                "text_observed" if words else "no_text_detected"),
            "words":words,"languages_used":used,
            "engine_rotation":best[1],"error_code":None,
        }
