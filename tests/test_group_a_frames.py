"""GA-MOD-07 frame planner, real PNG provenance and immutable storage tests."""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from intelligence.group_a.contracts import Scene
from intelligence.group_a.media_manifest import verify_file
from intelligence.group_a.extractors.technical import ProbeInfo, probe_details
from intelligence.group_a.extractors.frames import (
    FRAME_SCHEMA, FrameError, SampleConfig, _actual_pts, _equivalent,
    _perceptual, plan_timestamps, sample_frames,
)
from intelligence.group_a.extractors.frame_store import FrameStore

try:
    import jsonschema
    from PIL import Image
except ImportError:
    jsonschema = None
    Image = None


def png(color):
    with BytesIO() as out:
        Image.new("RGB",(90,50),color).save(out,format="PNG")
        return out.getvalue()


def info(duration="4.000", timebase="1/1000"):
    return ProbeInfo(Decimal(duration),"16:9",0,"mpeg4",timebase,
                     Decimal("0"),"stream")


def sample_source(root):
    path=Path(root)/"source.mp4"
    data=b"\x00\x00\x00\x18ftypisom"+bytes(200)
    path.write_bytes(data)
    return verify_file(str(path),media_kind="video",
                       sha256=sha256(data).hexdigest(),roots=[root])


def runner_factory(*, color_by_time=None, fail_after=None):
    count=[0]
    colors=color_by_time if color_by_time is not None else (
        lambda timestamp:"red" if timestamp<Decimal("2") else "blue"
    )
    def runner(args,**kwargs):
        count[0]+=1
        if fail_after is not None and count[0]>fail_after:
            return SimpleNamespace(returncode=1,stdout=b"",stderr=b"fake frame failure")
        vf=args[args.index("-vf")+1]
        m=re.search(r"gte\(t,([0-9]+\.[0-9]{3})\)",vf)
        assert m, vf
        requested=Decimal(m.group(1))
        ticks=int(requested*1000)
        return SimpleNamespace(
            returncode=0,stdout=png(colors(requested)),
            stderr=(f"[Parsed_showinfo_2] n: 0 pts: {ticks} "
                    f"pts_time:{requested:.3f} fmt:rgb24\n").encode("utf-8"),
        )
    return runner,count


@unittest.skipUnless(Image is not None,"Pillow required for frame QA")
class GA07FrameTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.media=sample_source(self.temp.name)

    def test_plan_is_deterministic_bounded_and_early_plus_scene_aware(self):
        scenes=(Scene(1,Decimal("0"),Decimal("1")),
                Scene(2,Decimal("1"),Decimal("3")),
                Scene(3,Decimal("3"),Decimal("12")))
        config=SampleConfig(budget=10,early_focus=3)
        first=plan_timestamps(Decimal("12"),scenes=scenes,config=config)
        self.assertEqual(first,plan_timestamps("12.000",scenes=scenes,config=config))
        self.assertEqual(first,tuple(sorted(set(first))))
        self.assertEqual(first[0],Decimal("0"))
        self.assertIn(Decimal("0.250"),first)
        self.assertIn(Decimal("0.750"),first)
        self.assertTrue(any(x>Decimal("6") for x in first))
        self.assertLessEqual(len(first),config.budget)
        with self.assertRaises(FrameError):
            plan_timestamps("601")
        self.assertEqual(SampleConfig(budget=1).budget,1)
        with self.assertRaises(FrameError):
            SampleConfig(budget=49)
        with self.assertRaises(FrameError):
            SampleConfig(budget=True)
        short=plan_timestamps("0.040",config=SampleConfig(budget=6))
        self.assertEqual(short,(Decimal("0"),))

    def test_scene_limit_and_strict_source_timebase(self):
        self.assertEqual(_actual_pts(
            "[showinfo] n:0 pts: 12288 pts_time:1.2",Decimal("1.2"),
            __import__("fractions").Fraction(1,10240),Decimal("4")),
            ("12288","1.200"))
        with self.assertRaises(FrameError):
            _actual_pts("[showinfo] pts:12 pts_time:100.0",
                        Decimal("0"),__import__("fractions").Fraction(1,1000),
                        Decimal("4"))
        with self.assertRaises(FrameError):
            _actual_pts("[showinfo] pts:1 pts_time:0.001\n[showinfo] pts:2 pts_time:0.002",
                        Decimal("0"),__import__("fractions").Fraction(1,1000),
                        Decimal("4"))
        self.assertNotEqual(_perceptual(png("red"))[1],_perceptual(png("blue"))[1])
        red=_perceptual(png("red"))
        blue=_perceptual(png("blue"))
        self.assertFalse(_equivalent(red[:2],blue[:2],SampleConfig()))

    def test_fake_ffmpeg_sparse_manifest_dedupe_and_source_sha(self):
        runner,count=runner_factory()
        manifest,payloads=sample_frames(
            self.media,info(),roots=[self.temp.name],
            config=SampleConfig(budget=8,max_edge=128),runner=runner,
            ffmpeg=__import__("sys").executable)
        self.assertEqual(manifest["schema_version"],FRAME_SCHEMA)
        self.assertEqual(manifest["source_media_sha256"],self.media.sha256)
        self.assertEqual(manifest["coverage"]["visual_temporal_coverage"],"unknown")
        self.assertEqual(manifest["coverage"]["ocr_temporal_coverage"],"unknown")
        self.assertGreater(manifest["coverage"]["duplicates_suppressed"],0)
        self.assertEqual(len(manifest["frames"]),2)
        self.assertEqual(len(payloads),2)
        self.assertEqual(manifest["coverage"]["requested"],count[0])
        self.assertEqual(manifest["coverage"]["not_observed"],0)
        self.assertEqual(manifest["coverage"]["requested"],
            manifest["coverage"]["retained"]+
            manifest["coverage"]["duplicates_suppressed"])
        for frame in manifest["frames"]:
            self.assertIn(frame["png_sha256"],frame["frame_ref"])
            self.assertEqual(len(frame["perceptual_hash"]),16)
            self.assertGreaterEqual(Decimal(frame["observed_sec"]),
                                    Decimal(frame["requested_sec"])-Decimal("0.002"))
        self.assertFalse(any("/" in x for x in payloads))
        self.assertNotIn(str(self.root),json.dumps(manifest))
        if jsonschema:
            schema=Path(__file__).resolve().parents[1] / "intelligence" / "group_a" / "schemas" / "group-a-frame-manifest-v1.json"
            doc=json.loads(schema.read_text("utf-8"))
            jsonschema.Draft202012Validator.check_schema(doc)
            jsonschema.validate(manifest,doc)

    def test_store_atomic_idempotence_and_no_blob_in_manifest(self):
        runner,_=runner_factory()
        manifest,payloads=sample_frames(
            self.media,info(),roots=[self.temp.name],
            config=SampleConfig(budget=6),runner=runner,
            ffmpeg=__import__("sys").executable)
        storage=FrameStore(str(self.root/"evidence"))
        project="a"*32
        run_id=str(uuid4())
        ref=storage.commit(project,run_id,manifest,payloads)
        self.assertTrue(ref.startswith("ga-frame-batch:"))
        self.assertEqual(storage.commit(project,run_id,manifest,payloads),ref)
        self.assertEqual(storage.read_manifest(project,run_id,manifest["manifest_sha256"]),manifest)
        f=manifest["frames"][0]
        content=storage.read_png(project,run_id,manifest["manifest_sha256"],f["frame_ref"])
        self.assertEqual(sha256(content).hexdigest(),f["png_sha256"])
        batch=self.root/"evidence"/project/run_id/manifest["manifest_sha256"]
        self.assertFalse(any(x.name.startswith(".frames-pending-") for x in batch.parent.iterdir()))
        self.assertNotIn("iVBOR",json.dumps(manifest))
        bad=batch/(f["frame_ref"][9:]+".png")
        bad.write_bytes(b"corrupted")
        with self.assertRaises(FrameError):
            storage.commit("../foreign",run_id,manifest,payloads)
        with self.assertRaises(FrameError):
            storage.commit(project,"../foreign",manifest,payloads)
        with self.assertRaises(FrameError):
            storage.commit(project,run_id,manifest,payloads)
        with self.assertRaises(FrameError):
            storage.read_png(project,run_id,manifest["manifest_sha256"],f["frame_ref"])

    def test_failure_cleanups_temp_and_never_publishes_manifest(self):
        runner,_=runner_factory()
        manifest,payloads=sample_frames(
            self.media,info(),roots=[self.temp.name],
            config=SampleConfig(budget=4),runner=runner,
            ffmpeg=__import__("sys").executable)
        store=FrameStore(str(self.root/"store"))
        project="a"*32
        run_id=str(uuid4())
        from intelligence.group_a.extractors import frame_store
        original=frame_store._write_fsync
        calls=[0]
        def injected(path,data):
            calls[0]+=1
            if calls[0]==2:
                raise OSError("write failed")
            return original(path,data)
        with patch.object(frame_store,"_write_fsync",injected):
            with self.assertRaises(OSError):
                store.commit(project,run_id,manifest,payloads)
        parent=self.root/"store"/project/run_id
        self.assertEqual(list(parent.iterdir()),[])
        with self.assertRaises(FrameError):
            store.read_manifest(project,run_id,manifest["manifest_sha256"])
        self.assertTrue(store.commit(project,run_id,manifest,payloads))

    def test_decoder_failure_no_false_complete_batch_and_changed_media(self):
        runner,_=runner_factory(fail_after=1)
        with self.assertRaises(FrameError) as ctx:
            sample_frames(self.media,info(),roots=[self.temp.name],
                          config=SampleConfig(budget=5),runner=runner,
                          ffmpeg=__import__("sys").executable)
        self.assertEqual(ctx.exception.code,"frame_decode_failed")
        src=Path(self.media.path)
        src.write_bytes(src.read_bytes()+b"mutated")
        with self.assertRaises(Exception):
            sample_frames(self.media,info(),roots=[self.temp.name],
                          runner=runner,ffmpeg=__import__("sys").executable)

    def test_same_color_suppression_but_different_color_kept(self):
        runner,_=runner_factory(color_by_time=lambda _: "black")
        manifest,_=sample_frames(
            self.media,info(),roots=[self.temp.name],
            config=SampleConfig(budget=12),runner=runner,
            ffmpeg=__import__("sys").executable)
        self.assertEqual(len(manifest["frames"]),1)
        self.assertEqual(manifest["coverage"]["visual_temporal_coverage"],"unknown")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe")
                     and Image is not None, "FFmpeg/FFprobe/Pillow required")
class GA07RealVideo(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def video(self,kind="cuts"):
        output=self.root/(kind+".mp4")
        ffmpeg=shutil.which("ffmpeg")
        if kind=="cuts":
            command=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","color=c=red:s=160x90:r=10:d=1",
                "-f","lavfi","-i","color=c=blue:s=160x90:r=10:d=1",
                "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map","[v]","-c:v","mpeg4","-q:v","3",str(output)]
        else:
            command=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","color=c=black:s=160x90:r=10:d=2",
                "-c:v","mpeg4","-q:v","3",str(output)]
        subprocess.run(command,check=True,capture_output=True,timeout=35)
        digest=sha256(output.read_bytes()).hexdigest()
        file=verify_file(str(output),media_kind="video",
                         sha256=digest,roots=[self.temp.name])
        proc=subprocess.run([shutil.which("ffprobe"),"-v","error",
                  "-select_streams","v:0",
                  "-show_entries",
                  "format=duration:stream=index,codec_type,codec_name,width,height,duration,sample_aspect_ratio,time_base,start_time",
                  "-of","json",str(output)],
                  check=True,capture_output=True,text=True,timeout=30)
        return file,probe_details(proc.stdout,strict_codec=True)

    def test_real_video_pts_color_change_and_atomic_persistence(self):
        file,details=self.video()
        manifest,payloads=sample_frames(
            file,details,roots=[self.temp.name],
            config=SampleConfig(budget=5,max_edge=128,early_focus=2))
        self.assertEqual(manifest["source_media_sha256"],file.sha256)
        self.assertGreaterEqual(len(manifest["frames"]),2)
        self.assertLessEqual(len(manifest["frames"]),5)
        self.assertTrue(all(Decimal(x["observed_sec"])<details.duration
                            for x in manifest["frames"]))
        store=FrameStore(str(self.root/"evidence"))
        run=str(uuid4())
        store.commit("a"*32,run,manifest,payloads)
        for frame in manifest["frames"]:
            self.assertTrue(store.read_png("a"*32,run,manifest["manifest_sha256"],
                                            frame["frame_ref"]).startswith(b"\x89PNG"))
        self.assertEqual(manifest["coverage"]["ocr_temporal_coverage"],"unknown")

    def test_real_low_light_video_preserves_sparse_truth(self):
        file,details=self.video("dark")
        manifest,payloads=sample_frames(
            file,details,roots=[self.temp.name],
            config=SampleConfig(budget=4,max_edge=128))
        self.assertGreaterEqual(len(payloads),1)
        self.assertEqual(manifest["coverage"]["visual_temporal_coverage"],"unknown")


if __name__=="__main__":
    unittest.main()
