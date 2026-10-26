"""GA-MOD-06 regression: exact A1 DTO plus strict offline video decoding.

All generated media uses synthetic FFmpeg sources; no downloads, provider calls
or database. Use injectable fake runners for rare failure/timebase scenarios.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import shutil
import subprocess
import sys
import unittest

from intelligence.group_a.contracts import (
    GroupAError, MAX_DURATION_SECONDS, MAX_SCENES, parse_config,
)
from intelligence.group_a.extractor import (
    measure as legacy_measure, inspect_probe as legacy_inspect,
    parse_cuts as legacy_parse,
)
from intelligence.group_a.extractors.technical import (
    analyze_technical, inspect_probe, parse_cuts, probe_details, METHOD_VERSION,
    ProbeInfo, TechnicalEvidence,
)


def probe(*, duration="5.000", start="4.000", sar="16:15",
          codec="mpeg4", timebase="1/12800", extra_streams=()):
    streams=[
        {"index":2,"codec_type":"video","codec_name":codec,"width":720,
         "height":576,"sample_aspect_ratio":sar,"time_base":timebase,
         "start_time":start,"duration":duration}
    ]
    return json.dumps({
        "streams":[*extra_streams,*streams],
        "format":{"duration":"7.000","start_time":start},
    })


def result(*, code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code,stdout=stdout,stderr=stderr)


class GATechnicalContracts(unittest.TestCase):
    def test_legacy_extractor_module_preserves_all_old_exports(self):
        from intelligence.group_a import extractor as legacy
        from intelligence.group_a.extractors import technical
        for name in ("MAX_DURATION_SECONDS","MAX_SCENES","Scene",
                     "TechnicalMeasurement","parse_config","seconds",
                     "PTS_RE","_binary","_execute","measure",
                     "inspect_probe","parse_cuts"):
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(legacy,name))
        self.assertIs(legacy.measure,technical.measure)
        self.assertIs(legacy.inspect_probe,technical.inspect_probe)
        self.assertIs(legacy.parse_cuts,technical.parse_cuts)

    def test_probe_display_aspect_duration_timebase_stream_identity(self):
        details=probe_details(probe())
        self.assertEqual(details.duration,Decimal("5.000"))
        self.assertEqual(details.aspect_ratio,"4:3")
        self.assertEqual(details.stream_index,2)
        self.assertEqual(details.time_base,"1/12800")
        self.assertEqual(details.start_time,Decimal("4.000"))
        self.assertEqual(details.codec_name,"mpeg4")
        self.assertEqual(details.duration_origin,"stream")
        self.assertEqual(inspect_probe(probe()),legacy_inspect(probe()))

    def test_attached_cover_is_not_main_video(self):
        cover={"index":0,"codec_type":"video","codec_name":"mjpeg",
               "width":200,"height":200,
               "disposition":{"attached_pic":1}}
        details=probe_details(probe(extra_streams=(cover,)),strict_codec=True)
        self.assertEqual(details.stream_index,2)
        with self.assertRaises(GroupAError):
            probe_details(probe(extra_streams=(
                dict(cover,index=0,disposition={"attached_pic":0}),
            )),strict_codec=True)

    def test_invalid_video_inputs_and_duration_limits(self):
        samples=[
            "not JSON", "[]", "{}", '{"streams":[{"codec_type":"audio"}]}',
            probe(duration="600.0004"), probe(duration="-1"),
            probe(duration="NaN"),probe(sar="0:1"),probe(sar="N/A"),
            probe(timebase="0/0"),probe(codec="~untrusted"),
        ]
        for raw in samples:
            with self.subTest(raw=raw[:95]),self.assertRaises(GroupAError):
                probe_details(raw,strict_codec=True)
        self.assertEqual(probe_details(probe(duration="600.000"),
                                       strict_codec=True).duration,
                         MAX_DURATION_SECONDS)
        with self.assertRaises(GroupAError) as exc:
            probe_details(probe(codec="unknowncodec"),strict_codec=True)
        self.assertEqual(exc.exception.code,"unsupported_codec")

    def test_duration_fallback_remains_compatible(self):
        raw=json.dumps({
            "streams":[{"codec_type":"video","width":1080,"height":1920,
                        "sample_aspect_ratio":"1:1","duration":"N/A"}],
            "format":{"duration":"5.250"},
        })
        info=probe_details(raw)
        self.assertEqual(info.duration_origin,"container")
        self.assertEqual((info.duration,info.aspect_ratio),
                         (Decimal("5.250"),"9:16"))

    def test_scene_debounce_sorted_pts_and_exact_upper_boundary(self):
        log="\n".join(
            f"[showinfo] pts_time:{v}" for v in
            ("3.000","1.001","1.000","0.000","0.015","4.999","2.000")
        )
        cuts=parse_cuts(log,Decimal("5.000"),Decimal("0.25"))
        self.assertEqual([(x.start_sec,x.end_sec) for x in cuts],[
            (Decimal("0"),Decimal("1.000")),
            (Decimal("1.000"),Decimal("2.000")),
            (Decimal("2.000"),Decimal("3.000")),
            (Decimal("3.000"),Decimal("5.000")),
        ])
        self.assertEqual(cuts,legacy_parse(log,Decimal("5.000"),Decimal("0.25")))
        many="\n".join(f"[showinfo] pts_time:{i}.000" for i in range(1,301))
        with self.assertRaises(GroupAError) as ctx:
            parse_cuts(many,Decimal("400.000"),Decimal("0.25"))
        self.assertEqual(ctx.exception.code,"too_many_scenes")
        okay="\n".join(f"[showinfo] pts_time:{i}.000" for i in range(1,300))
        self.assertEqual(len(parse_cuts(okay,Decimal("400.000"),Decimal("0.25"))),
                         MAX_SCENES)

    def test_strict_execution_never_invents_semantic_features(self):
        calls=[]
        def fake_runner(args, **kwargs):
            calls.append(args)
            if "-version" in args:
                return result(stdout=(
                    "ffprobe version 7.1 fixture\n" if "ffprobe" in str(args[0])
                    else "ffmpeg version 7.1 fixture\n"))
            if "-show_entries" in args:
                return result(stdout=probe())
            return result(stdout="frame=3\nprogress=end\n",
                          stderr="[showinfo] pts_time:0.000\n"
                                 "[showinfo] pts_time:2.000\n")
        with TemporaryDirectory() as td:
            path=Path(td)/"synthetic.mp4"
            path.write_bytes(b"synthetic")
            record=analyze_technical(path,parse_config(),runner=fake_runner,
                ffprobe=sys.executable,ffmpeg=sys.executable,
                strict=True,include_versions=False,roots=[td])
        self.assertIsInstance(record,TechnicalEvidence)
        self.assertEqual(record.method_version,METHOD_VERSION)
        self.assertEqual(record.decoded_frames,3)
        self.assertEqual(record.probe.stream_index,2)
        self.assertEqual(record.probe.start_time,Decimal("4.000"))
        self.assertEqual(record.measurement.aspect_ratio,"4:3")
        self.assertEqual(len(record.measurement.scenes),2)
        d=record.to_dict()
        self.assertEqual(d["analysis_state"],"partial")
        self.assertEqual(d["content_semantics"],"unknown")
        self.assertEqual(d["measurement"]["speech_status"],"not_run")
        self.assertEqual(d["measurement"]["has_subtitles"],"unknown")
        self.assertIsNone(d["measurement"]["cta_start_sec"])
        self.assertNotIn("fps",str(d).lower())
        self.assertNotIn("width",str(d).lower())
        self.assertNotIn("height",str(d).lower())
        video_cmd=calls[1]
        self.assertEqual(video_cmd[video_cmd.index("-map")+1],"0:2")
        self.assertIn("setpts=PTS-STARTPTS",video_cmd[video_cmd.index("-vf")+1])
        self.assertIn("eq(n,0)",video_cmd[video_cmd.index("-vf")+1])
        self.assertEqual(video_cmd[video_cmd.index("-progress")+1],"pipe:1")
        self.assertTrue(all("file,pipe" in command for command in calls))

    def test_no_progress_rejected_by_strict_but_legacy_mock_still_supported(self):
        def fake_runner(args,**kwargs):
            if "-show_entries" in args:
                return result(stdout=probe())
            return result(stdout="",stderr="[showinfo] pts_time:2.000\n")
        with TemporaryDirectory() as td:
            path=Path(td)/"fixture.mp4"
            path.write_bytes(b"fixture")
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(path,parse_config(),runner=fake_runner,
                    ffprobe=sys.executable,ffmpeg=sys.executable,strict=True,roots=[td])
            self.assertEqual(ctx.exception.code,"decode_unverified")
            measured=legacy_measure(path,parse_config(),runner=fake_runner,
                    ffprobe=sys.executable,ffmpeg=sys.executable)
            self.assertEqual(measured.duration_sec,Decimal("5.000"))
            self.assertEqual(len(measured.scenes),2)

    def test_strict_media_origin_and_symlink_is_rejected(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            f=root/"video.mp4"
            f.write_bytes(b"local source")
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(f,parse_config(),ffprobe=sys.executable,
                                  ffmpeg=sys.executable)
            self.assertEqual(ctx.exception.code,"media_roots_unconfigured")
            other=root/"untrusted"
            other.mkdir()
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(f,parse_config(),roots=[str(other)],
                                  ffprobe=sys.executable,ffmpeg=sys.executable)
            self.assertEqual(ctx.exception.code,"media_path_not_allowed")
            alias=root/"linked.mp4"
            try:
                alias.symlink_to(f)
            except (NotImplementedError,OSError):
                pass
            else:
                with self.assertRaises(GroupAError) as ctx:
                    analyze_technical(alias,parse_config(),roots=[td],
                                      ffprobe=sys.executable,ffmpeg=sys.executable)
                self.assertEqual(ctx.exception.code,"media_path_not_allowed")

    def test_media_changed_while_ffmpeg_runs_is_rejected(self):
        with TemporaryDirectory() as td:
            f=Path(td)/"video.mp4"
            f.write_bytes(b"before")
            def mutate(args,**kwargs):
                if "-show_entries" in args:
                    return result(stdout=probe())
                f.write_bytes(b"longer after mutation")
                return result(stdout="frame=2\\nprogress=end\\n",
                              stderr="[showinfo] pts_time:1.0\\n")
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(f,parse_config(),runner=mutate,
                    roots=[td],ffprobe=sys.executable,ffmpeg=sys.executable)
            self.assertEqual(ctx.exception.code,"media_changed")

    def test_tool_failure_timeout_missing_binary_and_malformed_log(self):
        with TemporaryDirectory() as td:
            path=Path(td)/"fixture.mp4"
            path.write_bytes(b"fixture")
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(path,parse_config(),ffprobe=str(path)+"missing",
                                  roots=[td])
            self.assertEqual(ctx.exception.code,"tool_not_installed")
            def bad_exit(args,**kwargs):
                return result(code=1,stderr="sensitive absolute path")
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(path,parse_config(),runner=bad_exit,
                     ffprobe=sys.executable,ffmpeg=sys.executable,roots=[td])
            self.assertEqual(ctx.exception.code,"extractor_failed")
            self.assertNotIn("sensitive",str(ctx.exception))
            def timeout(args,**kwargs):
                raise subprocess.TimeoutExpired(args,kwargs["timeout"])
            with self.assertRaises(GroupAError) as ctx:
                analyze_technical(path,parse_config(),runner=timeout,
                    ffprobe=sys.executable,ffmpeg=sys.executable,roots=[td])
            self.assertEqual(ctx.exception.code,"extractor_unavailable")
        with self.assertRaises(GroupAError):
            parse_cuts("x" * 10_000_001,Decimal("5"),Decimal("0.25"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Local FFmpeg+FFprobe not installed")
class GA06RealMedia(unittest.TestCase):
    def generate(self, path, *, scene=True, offset=None, fade=False):
        ffmpeg=shutil.which("ffmpeg")
        if scene:
            cmd=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-y",
                 "-f","lavfi","-i","color=c=red:s=360x640:r=25:d=1",
                 "-f","lavfi","-i","color=c=blue:s=360x640:r=25:d=1",
                 "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
                 "-map","[v]","-c:v","mpeg4","-q:v","3"]
        else:
            cmd=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-y",
                 "-f","lavfi","-i","color=c=red:s=360x640:r=25:d=2",
                 "-vf","fade=t=in:st=0:d=2" if fade else "null",
                 "-c:v","mpeg4","-q:v","3"]
        if offset is not None:
            cmd.extend(["-output_ts_offset",str(offset)])
        subprocess.run(cmd+[str(path)],check=True,capture_output=True,timeout=35)

    def test_abrupt_cut_and_evidence_metadata(self):
        with TemporaryDirectory() as td:
            path=Path(td)/"abrupt.mp4"
            self.generate(path)
            observation=analyze_technical(path,parse_config(),strict=True,
                                          include_versions=True,roots=[td])
            self.assertEqual(observation.measurement.duration_sec,Decimal("2.000"))
            self.assertEqual(observation.measurement.aspect_ratio,"9:16")
            self.assertGreaterEqual(len(observation.measurement.scenes),2)
            self.assertEqual(observation.measurement.scenes[-1].end_sec,
                             Decimal("2.000"))
            self.assertGreater(observation.decoded_frames,0)
            self.assertIsNotNone(observation.probe.time_base)
            self.assertTrue(observation.ffprobe_version.startswith("ffprobe version"))
            self.assertTrue(observation.ffmpeg_version.startswith("ffmpeg version"))
            self.assertEqual(observation.measurement.to_dict()["scene_status"],"complete")

    def test_static_and_gradual_no_fake_scene(self):
        with TemporaryDirectory() as td:
            for fade in (False,True):
                path=Path(td)/("fade.mp4" if fade else "static.mp4")
                self.generate(path,scene=False,fade=fade)
                analysis=analyze_technical(path,parse_config(),strict=True,roots=[td])
                self.assertEqual(analysis.measurement.duration_sec,Decimal("2.000"))
                self.assertGreater(analysis.decoded_frames,0)
                self.assertTrue(1<=len(analysis.measurement.scenes)<=MAX_SCENES)
                self.assertEqual(analysis.measurement.scenes[0].start_sec,
                                 Decimal("0"))
                self.assertEqual(analysis.measurement.scenes[-1].end_sec,
                                 Decimal("2.000"))

    def test_nonzero_container_start_is_normalized(self):
        with TemporaryDirectory() as td:
            path=Path(td)/"offset.mp4"
            self.generate(path,offset=3)
            analysis=analyze_technical(path,parse_config(),strict=True,roots=[td])
            self.assertEqual(analysis.measurement.duration_sec,Decimal("2.000"))
            self.assertGreaterEqual(len(analysis.measurement.scenes),2)
            self.assertEqual(analysis.measurement.scenes[0].start_sec,
                             Decimal("0"))
            self.assertTrue(all(
                Decimal("0")<=s.start_sec<s.end_sec<=Decimal("2.000")
                for s in analysis.measurement.scenes
            ))

    def test_audio_only_real_file_rejected(self):
        with TemporaryDirectory() as td:
            path=Path(td)/"audio_only.mp4"
            subprocess.run([
                shutil.which("ffmpeg"),"-nostdin","-hide_banner","-loglevel","error",
                "-y","-f","lavfi","-i","sine=frequency=440:duration=1",
                "-c:a","aac",str(path),
            ],check=True,capture_output=True,timeout=30)
            with self.assertRaises(GroupAError):
                analyze_technical(path,parse_config(),strict=True,roots=[td])


if __name__=="__main__":
    unittest.main()
