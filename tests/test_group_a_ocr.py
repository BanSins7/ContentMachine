"""GA12 OCR adversarial and real synthetic PNG regression (no DB/peers)."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
import json
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.frames import _perceptual
from intelligence.group_a.extractors.ocr import (
    OCRConfig, OCRError, OCRCache, analyze_frame_ocr, OCR_SCHEMA,
)
from intelligence.group_a.extractors.tesseract_ocr import (
    TesseractAdapter, tesseract_fingerprint, _orig_bbox, _parse_tsv,
)

try:
    import jsonschema
    from PIL import Image,ImageDraw,ImageFont
except ImportError:
    jsonschema=None
    Image=None


PROJECT="a"*32
RUN=str(uuid4())


def png(text=None,*,font=None,rotation=0,color="black",size=(450,160),point=37):
    if Image is None:
        raise unittest.SkipTest("Pillow required")
    im=Image.new("RGB",size,"white")
    if text is not None:
        painter=ImageDraw.Draw(im)
        if font is None:
            painter.text((20,42),text,fill=color)
        else:
            painter.text((20,42),text,font=ImageFont.truetype(str(font),point),
                         fill=color)
    if rotation:
        im=im.rotate(rotation,expand=True)
    with BytesIO() as out:
        im.save(out,"PNG")
        return out.getvalue()


def make_batch(images,*,times=None):
    times=times if times is not None else [f"{i*0.3:.3f}" for i in range(len(images))]
    if len(images)!=len(times):
        raise AssertionError("bad fixture")
    frames=[];blobs={}
    for idx,(buf,time) in enumerate(zip(images,times),1):
        digest=sha256(buf).hexdigest()
        name=f"f{idx:03d}-{digest}"
        dhash,_,width,height=_perceptual(buf)
        frames.append({
            "sample_index":idx,"requested_sec":time,
            "observed_pts_ticks":str(int(float(time)*1000)),
            "observed_sec":time,"frame_ref":"ga-frame:"+name,
            "png_sha256":digest,"png_byte_size":len(buf),"width":width,
            "height":height,"perceptual_hash":f"{dhash:016x}",
        })
        blobs[name+".png"]=buf
    manifest={
        "schema_version":"group-a-frame-manifest/v1",
        "method_version":"group-a-frame-sampling-v1",
        "source_media_sha256":"b"*64,
        "source_byte_size":99999,
        "source_duration_sec":"2.000",
        "source_stream_index":0,"source_timebase":"1/1000",
        "config":{"budget":len(images),"max_edge":512,
                  "early_focus":3,"duplicate_hamming":5,
                  "duplicate_color_l1":24},
        "coverage":{"type":"sparse_observations",
                    "visual_temporal_coverage":"unknown",
                    "ocr_temporal_coverage":"unknown",
                    "requested":len(images),"retained":len(images),
                    "duplicates_suppressed":0,"not_observed":0,
                    "sampled_span_sec":f"{float(times[-1])-float(times[0]):.3f}",
                    "unsampled_intervals_present":True},
        "frames":frames,
    }
    return {**manifest,"manifest_sha256":canonical_sha256(manifest)},blobs


def recognized(text="Привет",*,bbox=None,line="l000001",confidence="93.72"):
    return {"text":text,"confidence_pct":confidence,
            "bbox":bbox or {"x":20,"y":30,"width":80,"height":25},
            "line_ref":line}


class FakeBackend:
    identity={"engine":"tesseract","engine_version":"5.5.0",
              "model_sha256":"c"*64,"device":"cpu"}

    def __init__(self,outputs):
        self.outputs=list(outputs)
        self.calls=0
    def process(self,blob,cfg):
        pos=self.calls
        self.calls+=1
        item=self.outputs[pos]
        if isinstance(item,Exception):
            raise item
        return item


def output(words,*,state=None,langs=("eng","rus"),rotation=0):
    return {"state":state or ("text_observed" if words else "no_text_detected"),
            "words":words,"languages_used":list(langs),
            "engine_rotation":rotation,"error_code":None}


@unittest.skipUnless(Image is not None,"Pillow required")
class GA12OCRCore(unittest.TestCase):
    def test_ru_en_geometry_original_pts_and_sparse_truth(self):
        manifest,blobs=make_batch([png("Привет"),png("Hello world")],
                                  times=["0.150","1.250"])
        engine=FakeBackend([output([recognized("Привет")]),
                            output([recognized("Hello",line="l000002")])])
        result=analyze_frame_ocr(manifest,blobs,backend=engine,project_id=PROJECT,
                                 run_id=RUN)
        self.assertEqual(result["schema_version"],OCR_SCHEMA)
        self.assertEqual(result["state"],"partial")
        self.assertEqual(result["frames"][0]["observed_sec"],"0.150")
        self.assertEqual(result["frames"][1]["observed_sec"],"1.250")
        self.assertEqual(result["frames"][0]["frame_ref"],
                         manifest["frames"][0]["frame_ref"])
        self.assertEqual(result["frames"][0]["words"][0]["bbox"]["x"],20)
        self.assertEqual(result["frames"][0]["words"][0]["text"],"Привет")
        self.assertEqual(result["frames"][1]["words"][0]["text"],"Hello")
        self.assertEqual(result["counts"]["observed"],2)
        self.assertEqual(result["temporal_coverage"],"unknown")
        self.assertEqual(result["screen_text_absence"],"unknown")
        self.assertTrue(result["unsampled_intervals_present"])
        self.assertEqual(result["observation_sha256"],
            canonical_sha256({k:v for k,v in result.items()
                              if k!="observation_sha256"}))
        self.assertNotIn("screen_text_intervals",result)

    def test_no_text_on_single_frame_does_not_prove_full_video_absence(self):
        manifest,blobs=make_batch([png()])
        engine=FakeBackend([output([])])
        result=analyze_frame_ocr(manifest,blobs,backend=engine,project_id=PROJECT,
                                 run_id=RUN)
        self.assertEqual(result["frames"][0]["state"],"no_text_detected")
        self.assertEqual(result["frames"][0]["text_status"],
                         "no_text_detected_on_this_frame_only")
        self.assertEqual(result["screen_text_absence"],"unknown")
        self.assertEqual(result["counts"]["no_text_detected"],1)

    def test_noisy_frame_failures_never_prove_text_absence(self):
        from random import Random
        im=Image.new("RGB",(140,100),"white")
        rng=Random(12)
        for y in range(100):
            for x in range(140):
                v=rng.randrange(256)
                im.putpixel((x,y),(v,v,v))
        with BytesIO() as out:
            im.save(out,"PNG")
            data=out.getvalue()
        manifest,blobs=make_batch([data])
        result=analyze_frame_ocr(manifest,blobs,project_id=PROJECT,run_id=RUN,
            backend=FakeBackend([OCRError("ocr_engine_failed")]))
        self.assertEqual(result["frames"][0]["state"],"failed")
        self.assertEqual(result["frames"][0]["text_status"],"unknown")
        self.assertEqual(result["screen_text_absence"],"unknown")

    def test_budget_and_partial_failure_are_explicit(self):
        manifest,blobs=make_batch([png("one"),png("two"),png("three")])
        engine=FakeBackend([output([recognized("Text")]),
                            OCRError("ocr_engine_timeout")])
        out=analyze_frame_ocr(manifest,blobs,backend=engine,project_id=PROJECT,
                              run_id=RUN,config=OCRConfig(frame_budget=2))
        self.assertEqual([x["state"] for x in out["frames"]],
                         ["text_observed","failed","skipped_budget"])
        self.assertEqual(out["counts"]["attempted"],2)
        self.assertEqual(out["counts"]["observed"],1)
        self.assertEqual(out["counts"]["failed"],1)
        self.assertEqual(out["counts"]["skipped_budget"],1)
        self.assertEqual(out["state"],"partial")
        self.assertEqual(out["frames"][1]["text_status"],"unknown")
        self.assertEqual(out["frames"][2]["text_status"],"unknown")

    def test_unavailable_backend_and_missing_language_are_not_absent_text(self):
        manifest,blobs=make_batch([png("Hello")])
        missing=analyze_frame_ocr(manifest,blobs,project_id=PROJECT,run_id=RUN)
        self.assertEqual(missing["state"],"unavailable")
        self.assertEqual(missing["frames"][0]["state"],"unavailable")
        self.assertEqual(missing["frames"][0]["text_status"],"unknown")
        engine=FakeBackend([OCRError("ocr_language_unavailable")])
        failed=analyze_frame_ocr(manifest,blobs,backend=engine,
                                 project_id=PROJECT,run_id=RUN)
        self.assertEqual(failed["frames"][0]["state"],"unavailable")
        self.assertEqual(failed["frames"][0]["reason_code"],"ocr_language_unavailable")

    def test_known_language_fallback_is_marked_partial(self):
        manifest,blobs=make_batch([png("Hello")])
        result=analyze_frame_ocr(manifest,blobs,project_id=PROJECT,run_id=RUN,
            backend=FakeBackend([output([recognized("Hello")],
                                        state="partial_language",langs=("eng",))]),
            config=OCRConfig(missing_language="fallback_eng"))
        self.assertEqual(result["frames"][0]["state"],"partial_language")
        self.assertEqual(result["frames"][0]["languages_used"],["eng"])
        self.assertEqual(result["counts"]["partial_language"],1)
        rejected=analyze_frame_ocr(manifest,blobs,project_id=PROJECT,run_id=RUN,
            backend=FakeBackend([output([recognized("Hello")],langs=("eng",))]))
        self.assertEqual(rejected["frames"][0]["state"],"failed")
        self.assertEqual(rejected["frames"][0]["reason_code"],
                         "ocr_unreported_language_gap")

    def test_bad_frame_bytes_and_manifest_digests_rejected_first(self):
        manifest,blobs=make_batch([png("Verified")])
        key=next(iter(blobs))
        fake=FakeBackend([output([recognized("Text")])])
        damaged=dict(blobs)
        damaged[key]=damaged[key]+b"changed"
        with self.assertRaises(Exception):
            analyze_frame_ocr(manifest,damaged,project_id=PROJECT,run_id=RUN,
                              backend=fake)
        self.assertEqual(fake.calls,0)
        forged=dict(manifest,source_media_sha256="f"*64)
        with self.assertRaises(Exception):
            analyze_frame_ocr(forged,blobs,project_id=PROJECT,run_id=RUN,
                              backend=fake)
        self.assertEqual(fake.calls,0)

    def test_bounding_boxes_confidence_and_model_outputs_fail_closed(self):
        manifest,blobs=make_batch([png("text")])
        malformed=[
            output([recognized("invisible",bbox={"x":449,"y":0,"width":20,"height":10})]),
            output([recognized("fake",confidence="NaN")]),
            output([recognized("fake",confidence="101.00")]),
            output([recognized("")]),
            output([recognized("text")],state="no_text_detected"),
            output([],state="text_observed"),
            output([recognized("text",line="../evil")]),
        ]
        for item in malformed:
            with self.subTest(value=str(item)[:75]):
                result=analyze_frame_ocr(manifest,blobs,project_id=PROJECT,
                    run_id=RUN,backend=FakeBackend([item]))
                self.assertEqual(result["frames"][0]["state"],"failed")
                self.assertEqual(result["screen_text_absence"],"unknown")
        with self.assertRaises(OCRError):
            OCRConfig(frame_budget=True)
        with self.assertRaises(OCRError):
            OCRConfig(device="cuda")
        with self.assertRaises(OCRError):
            OCRConfig(rotations=(90,90))

    def test_cache_bound_to_scope_model_and_rights_every_hit(self):
        manifest,blobs=make_batch([png("cache")])
        cache=OCRCache()
        used=[]
        def verifier(context):
            used.append(context)
            return True
        a=analyze_frame_ocr(manifest,blobs,backend=FakeBackend([
            output([recognized("Hello")])]),project_id=PROJECT,run_id=RUN,
            cache=cache,verify_current=verifier)
        fake=FakeBackend([RuntimeError("must not rerun")])
        b=analyze_frame_ocr(manifest,blobs,backend=fake,
              project_id=PROJECT,run_id=RUN,cache=cache,verify_current=verifier)
        self.assertEqual(fake.calls,0)
        self.assertEqual(b["counts"]["cached"],1)
        self.assertEqual(a["frames"][0]["words"][0]["text"],"Hello")
        b["frames"][0]["words"][0]["text"]="tamper"
        c=analyze_frame_ocr(manifest,blobs,backend=fake,project_id=PROJECT,
            run_id=RUN,cache=cache,verify_current=verifier)
        self.assertEqual(c["frames"][0]["words"][0]["text"],"Hello")
        self.assertEqual(len(used),3)
        self.assertEqual(used[0]["source_media_sha256"],manifest["source_media_sha256"])
        with self.assertRaises(OCRError):
            analyze_frame_ocr(manifest,blobs,backend=fake,
               project_id=PROJECT,run_id=RUN,cache=cache,
               verify_current=lambda _:False)
        self.assertEqual(len(cache._memo),0)
        with self.assertRaises(OCRError):
            analyze_frame_ocr(manifest,blobs,backend=fake,
                project_id=PROJECT,run_id=RUN,cache=cache)
        self.assertNotEqual(OCRCache.key(PROJECT,RUN,manifest,manifest["frames"][0],
                                        FakeBackend.identity,OCRConfig()),
            OCRCache.key("f"*32,RUN,manifest,manifest["frames"][0],
                         FakeBackend.identity,OCRConfig()))
        self.assertNotEqual(OCRCache.key(PROJECT,RUN,manifest,manifest["frames"][0],
                                        FakeBackend.identity,OCRConfig()),
            OCRCache.key(PROJECT,RUN,manifest,manifest["frames"][0],
                         FakeBackend.identity,OCRConfig(preprocess="sharpen")))

    def test_invalid_project_run_and_device_mismatch(self):
        manifest,blobs=make_batch([png("x")])
        for project,run in ((True,RUN),("a"*32,"../bad"),("../bad",RUN)):
            with self.assertRaises(OCRError):
                analyze_frame_ocr(manifest,blobs,project_id=project,run_id=run)
        engine=FakeBackend([output([recognized("test")])])
        engine.identity=dict(engine.identity,device="cuda")
        with self.assertRaises(OCRError):
            analyze_frame_ocr(manifest,blobs,backend=engine,
                             project_id=PROJECT,run_id=RUN)


@unittest.skipUnless(Image is not None,"Pillow required")
class GA12TesseractUnit(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        binary=self.root/"fake-tesseract"
        binary.write_bytes(b"not an executable, injected runner only")
        self.binary=str(binary)
        self.tessdata=self.root/"tessdata"
        self.tessdata.mkdir()
        (self.tessdata/"eng.traineddata").write_bytes(b"eng signed test")
        (self.tessdata/"rus.traineddata").write_bytes(b"rus signed test")
        self.model,_=tesseract_fingerprint(self.binary,str(self.tessdata))

    def make(self,raw):
        def run(cmd,**kwargs):
            if "--version" in cmd:
                return SimpleNamespace(returncode=0,stdout=b"",
                          stderr=b"tesseract 5.5.0\nleptonica-1.85.0\n")
            return SimpleNamespace(returncode=0,stdout=raw,
                                   stderr=b"Estimated resolution as 150")
        return TesseractAdapter(binary=self.binary,tessdata_dir=str(self.tessdata),
             expected_sha256=self.model,runner=run)

    def test_fake_tsv_english_russian_boxes_and_preprocess(self):
        raw=(b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
             b"1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t-1\t\n"
             b"5\t1\t1\t1\t1\t1\t40\t60\t160\t50\t93.72\tHello\n")
        backend=self.make(raw)
        observed=backend.process(png("Hello"),OCRConfig(languages=("eng",)))
        self.assertEqual(observed["state"],"text_observed")
        word=observed["words"][0]
        self.assertEqual(word["bbox"],{"x":20,"y":30,"width":80,"height":25})
        self.assertEqual(word["confidence_pct"],"93.72")
        self.assertEqual(word["text"],"Hello")
        self.assertEqual(backend.identity["engine"],"tesseract")
        self.assertEqual(backend.identity["model_sha256"],self.model)

    def test_rotated_bounding_box_maps_to_original_source(self):
        # 90 degrees CCW: original image W=450, H=160.
        # Rotated bbox x20 y40 w50 h30 => original x380 y20 w30 h50.
        observed=_orig_bbox(20,40,50,30,1,90,450,160)
        self.assertEqual(observed,{"x":380,"y":20,"width":30,"height":50})
        self.assertEqual(_orig_bbox(20,40,50,30,1,180,450,160),
                         {"x":380,"y":90,"width":50,"height":30})
        with self.assertRaises(OCRError):
            _orig_bbox(460,0,20,20,1,0,450,160)
        with self.assertRaises(OCRError):
            _orig_bbox(20,40,50,30,1,12,450,160)

    def test_missing_ru_pack_and_explicit_english_fallback(self):
        (self.tessdata/"rus.traineddata").unlink()
        new_sha,_=tesseract_fingerprint(self.binary,str(self.tessdata))
        tsv=(b"level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"
             b"5\t1\t1\t1\t20\t30\t50\t20\t93\tHello\n")
        self.model=new_sha
        backend=self.make(tsv)
        with self.assertRaises(OCRError) as ctx:
            backend.process(png("Hello"),OCRConfig(languages=("eng","rus")))
        self.assertEqual(ctx.exception.code,"ocr_language_unavailable")
        result=backend.process(png("Hello"),
            OCRConfig(languages=("eng","rus"),missing_language="fallback_eng"))
        self.assertEqual(result["state"],"partial_language")
        self.assertEqual(result["languages_used"],["eng"])

    def test_tessdata_mutation_is_detected_before_cache_identity(self):
        adapter=self.make(b"")
        (self.tessdata/"eng.traineddata").write_bytes(b"forged bytes")
        with self.assertRaises(OCRError) as ctx:
            adapter.identity
        self.assertEqual(ctx.exception.code,"ocr_model_mismatch")

    def test_invalid_tsv_and_untrusted_geometry(self):
        adapter=self.make(b"garbage,not,tsv\n")
        manifest,blobs=make_batch([png("Hello")])
        result=analyze_frame_ocr(manifest,blobs,
            backend=adapter,project_id=PROJECT,run_id=RUN)
        self.assertEqual(result["frames"][0]["state"],"failed")
        self.assertEqual(result["frames"][0]["reason_code"],"ocr_tsv_invalid")

    def test_tesseract_timeout_does_not_become_empty_result(self):
        def run(cmd,**kwargs):
            if "--version" in cmd:
                return SimpleNamespace(returncode=0,stdout=b"tesseract 5.5.0\n",stderr=b"")
            raise subprocess.TimeoutExpired(cmd,kwargs["timeout"])
        adapter=TesseractAdapter(binary=self.binary,tessdata_dir=str(self.tessdata),
                                expected_sha256=self.model,runner=run)
        manifest,blobs=make_batch([png("Wait")])
        result=analyze_frame_ocr(manifest,blobs,backend=adapter,
                                 project_id=PROJECT,run_id=RUN)
        self.assertEqual(result["frames"][0]["state"],"failed")
        self.assertEqual(result["frames"][0]["text_status"],"unknown")


@unittest.skipUnless(jsonschema is not None and Image is not None,
                     "jsonschema and Pillow are test-only")
class GA12SchemaTests(unittest.TestCase):
    def test_sparse_ocr_wire_schema_and_unknown_not_false(self):
        path=Path(__file__).resolve().parents[1]/"intelligence"/"group_a"/"schemas"/"ga-frame-ocr-v1.json"
        doc=json.loads(path.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(doc)
        manifest,blobs=make_batch([png(),png()])
        outputs=[
            analyze_frame_ocr(manifest,blobs,project_id=PROJECT,run_id=RUN),
            analyze_frame_ocr(manifest,blobs,backend=FakeBackend([
                output([recognized("Hello")]),output([])]),
                project_id=PROJECT,run_id=RUN),
            analyze_frame_ocr(manifest,blobs,backend=FakeBackend([
                output([recognized("Hello")])]),config=OCRConfig(frame_budget=1),
                project_id=PROJECT,run_id=RUN),
        ]
        for result in outputs:
            jsonschema.validate(result,doc)
        forged=dict(outputs[1],screen_text_absence="verified_absent")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,doc)
        forged=dict(outputs[1],temporal_coverage="complete")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,doc)


def _local_tess_environment():
    binary=shutil.which("tesseract")
    possible=list(Path("/usr/share/tesseract-ocr").glob("*/tessdata")) if Path("/usr/share/tesseract-ocr").exists() else []
    data=next((x for x in possible if (x/"eng.traineddata").is_file()),None)
    fonts=list(Path("/usr/share/fonts").rglob("DejaVuSans.ttf")) if Path("/usr/share/fonts").exists() else []
    return binary,data,fonts[0] if fonts else None


@unittest.skipUnless(Image is not None,"Pillow required")
class GA12RealTesseract(unittest.TestCase):
    def setUp(self):
        binary,data,font=_local_tess_environment()
        if not (binary and data and font):
            self.skipTest("local Tesseract5 traineddata / font missing")
        self.binary,self.data,self.font=binary,data,font
        version=subprocess.run([binary,"--version"],capture_output=True,
                              timeout=10,text=True)
        if "tesseract 5." not in version.stdout+version.stderr:
            self.skipTest("local Tesseract5 not installed")

    def adapter(self):
        model,_=tesseract_fingerprint(self.binary,str(self.data))
        return TesseractAdapter(binary=self.binary,tessdata_dir=str(self.data),
                                expected_sha256=model)

    def test_real_eng_and_rus_text(self):
        backend=self.adapter()
        languages=("eng","rus") if (self.data/"rus.traineddata").exists() else ("eng",)
        cfg=OCRConfig(languages=languages,scale=2,psm=6)
        for lang,text in (("eng","HELLO WORLD"),("rus","ПРИВЕТ МИР")):
            if lang not in languages:
                continue
            with self.subTest(lang=lang):
                buf=png(text,font=self.font,size=(650,180),point=47)
                got=backend.process(buf,cfg)
                self.assertIn(got["state"],("text_observed","partial_language"))
                full=" ".join(w["text"] for w in got["words"]).upper()
                self.assertIn("HELLO" if lang=="eng" else "ПРИВЕТ",full)
                self.assertTrue(all(0<=w["bbox"]["x"]<650
                                    for w in got["words"]))

    def test_real_tiny_subtitle_rotation_and_empty(self):
        backend=self.adapter()
        cfg=OCRConfig(languages=("eng",),rotations=(0,270),
                      scale=2,psm=6)
        rotated=png("ROTATED TEXT",font=self.font,
                    size=(440,120),point=36,rotation=90)
        observed=backend.process(rotated,cfg)
        self.assertIn("ROTATED"," ".join(w["text"] for w in observed["words"]).upper())
        self.assertTrue(all(w["bbox"]["x"]+w["bbox"]["width"]<=120
                            for w in observed["words"]))
        small=png("Hello subtitle",font=self.font,
                  size=(450,160),point=22)
        got=backend.process(small,OCRConfig(languages=("eng",),scale=2))
        self.assertIn("Hello"," ".join(w["text"] for w in got["words"]))
        blank=backend.process(png(),OCRConfig(languages=("eng",)))
        self.assertEqual(blank["state"],"no_text_detected")


if __name__=="__main__":
    unittest.main()
