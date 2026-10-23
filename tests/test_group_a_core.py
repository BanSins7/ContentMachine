"""Offline QA of Group A stage A1; no PostgreSQL, FFmpeg or network required."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import os
import sys
import unittest
import shutil
import subprocess

from intelligence.group_a.contracts import (
    GroupAError, Scene, TechnicalMeasurement, parse_config, seconds
)
from intelligence.group_a.extractor import (
    authorized_file, inspect_probe, parse_cuts, measure
)


class GroupAContracts(unittest.TestCase):
    def test_configuration_strict(self):
        self.assertEqual(parse_config(),{
            "schema_version":"group-a-config/v1",
            "scene_threshold":"0.350","min_scene_gap_sec":"0.250"})
        for value in ({"upload_to_ai":True},{"scene_threshold":True},
                      {"scene_threshold":float("nan")},{"scene_threshold":-0.1},
                      {"min_scene_gap_sec":0},{"scene_threshold":1.0},[]):
            with self.subTest(value=value),self.assertRaises(GroupAError):
                parse_config(value)

    def test_probe_real_display_aspect_sar(self):
        raw=json.dumps({"streams":[{"codec_type":"video","width":1080,"height":1920,
                  "sample_aspect_ratio":"1:1"}],"format":{"duration":"10.257"}})
        self.assertEqual(inspect_probe(raw),(Decimal("10.257"),"9:16"))
        raw=json.dumps({"streams":[{"codec_type":"video","width":720,"height":576,
                  "sample_aspect_ratio":"16:15"}],"format":{"duration":"11.0"}})
        self.assertEqual(inspect_probe(raw),(Decimal("11.000"),"4:3"))

    def test_stream_duration_na_falls_back_to_container_duration(self):
        raw=json.dumps({"streams":[{"codec_type":"video","width":1080,
                  "height":1920,"sample_aspect_ratio":"1:1","duration":"N/A"}],
                  "format":{"duration":"5.255"}})
        self.assertEqual(inspect_probe(raw),(Decimal("5.255"),"9:16"))

    def test_probe_rejects_audio_only_and_unknown_duration(self):
        for raw in ('not json','{"streams":[]}',
                    '{"streams":[{"codec_type":"audio"}]}',
                    '{"streams":[{"codec_type":"video","width":720,"height":1280}]}',
                    '{"streams":[{"codec_type":"video","width":720,"height":1280}],"format":{"duration":"NaN"}}',
                    '{"streams":[{"codec_type":"video","width":720,"height":1280}],"format":{"duration":"10000"}}'):
            with self.subTest(raw=raw),self.assertRaises(GroupAError):
                inspect_probe(raw)

    def test_scene_boundaries_and_debouncing(self):
        log=("[Parsed_showinfo_1] n: 0 pts: 10240 pts_time:1.000\n"
             "[Parsed_showinfo_1] n: 1 pts: 10560 pts_time:1.031\n"
             "[Parsed_showinfo_1] n: 2 pts: 30720 pts_time:3.000\n"
             "[Parsed_showinfo_1] n: 3 pts: 46080 pts_time:4.500\n")
        cuts=parse_cuts(log,Decimal("5.000"),Decimal("0.25"))
        self.assertEqual([(s.start_sec,s.end_sec) for s in cuts],
                         [(Decimal("0"),Decimal("1.000")),
                          (Decimal("1.000"),Decimal("3.000")),
                          (Decimal("3.000"),Decimal("4.500")),
                          (Decimal("4.500"),Decimal("5.000"))])
        self.assertEqual(len(parse_cuts("",Decimal("5"),Decimal("0.25"))),1)

    def test_measurement_does_not_claim_unobserved_data(self):
        m=TechnicalMeasurement(Decimal("4.000"),"9:16",(
            Scene(1,Decimal("0"),Decimal("2")),Scene(2,Decimal("2"),Decimal("4.000"))))
        d=m.to_dict()
        self.assertEqual(d["scene_status"],"complete")
        self.assertEqual(d["speech_status"],"not_run")
        self.assertIsNone(d["spoken_words"])
        self.assertIsNone(d["cta_start_sec"])
        with self.assertRaises(GroupAError):
            TechnicalMeasurement(Decimal("4.000"),"9:16",(
                Scene(1,Decimal("0"),Decimal("1")),Scene(2,Decimal("2"),Decimal("4"))))

    def test_media_path_escapes_symlink_rejected(self):
        with TemporaryDirectory() as root,TemporaryDirectory() as outside:
            in_file=Path(root)/"in.mp4"
            in_file.write_bytes(b"dummy")
            out_file=Path(outside)/"out.mp4"
            out_file.write_bytes(b"not authorized")
            self.assertEqual(authorized_file(str(in_file),roots=[root]),in_file.resolve())
            with self.assertRaises(GroupAError):
                authorized_file(str(out_file),roots=[root])
            link=Path(root)/"escape.mp4"
            try:
                link.symlink_to(out_file)
            except OSError:
                pass
            else:
                with self.assertRaises(GroupAError):
                    authorized_file(str(link),roots=[root])
            with self.assertRaises(GroupAError):
                authorized_file(str(in_file),roots=[])

    def test_mocked_ffmpeg_no_external_apis(self):
        with TemporaryDirectory() as root:
            file=Path(root)/"fixture.mp4"
            file.write_bytes(b"fixture")
            observed=[]
            def runner(args,**kwargs):
                observed.append(args)
                if "-show_entries" in args:
                    return SimpleNamespace(returncode=0,stderr="",stdout=json.dumps({
                        "streams":[{"codec_type":"video","width":1080,"height":1920}],
                        "format":{"duration":"5.0"}}))
                return SimpleNamespace(returncode=0,stdout="",
                    stderr="[Parsed_showinfo_1] n:1 pts:30 pts_time:2.5\n")
            m=measure(file,parse_config(),runner=runner,ffprobe=sys.executable,
                      ffmpeg=sys.executable)
            # The injected binary path may have another name: first command
            # is disambiguated by '-show_entries', not the executable filename.
            self.assertEqual(m.duration_sec,Decimal("5.000"))
            self.assertEqual(len(m.scenes),2)
            self.assertTrue(all("-protocol_whitelist" in x for x in observed))
            self.assertTrue(all("file,pipe" in x for x in observed))


    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Local FFmpeg + FFprobe not installed")
    def test_real_local_video_smoke_detects_color_cut(self):
        # Generate a tiny synthetic clip in this disposable temp folder;
        # no platforms, network or user media involved.
        with TemporaryDirectory() as root:
            path=Path(root)/"real.mp4"
            cmd=[shutil.which("ffmpeg"),"-hide_banner","-loglevel","error","-y",
                 "-f","lavfi","-i","color=c=red:s=360x640:r=25:d=1",
                 "-f","lavfi","-i","color=c=blue:s=360x640:r=25:d=1",
                 "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
                 "-map","[v]","-c:v","mpeg4","-q:v","3",str(path)]
            subprocess.run(cmd,check=True,capture_output=True,timeout=30)
            checked=authorized_file(str(path),roots=[root])
            measured=measure(checked,parse_config())
            self.assertEqual(measured.duration_sec,Decimal("2.000"))
            self.assertEqual(measured.aspect_ratio,"9:16")
            self.assertGreaterEqual(len(measured.scenes),2)
            self.assertEqual(measured.scenes[-1].end_sec,Decimal("2.000"))


if __name__=="__main__":
    unittest.main()
