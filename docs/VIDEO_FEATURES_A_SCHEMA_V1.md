# Video Feature Data A — PostgreSQL schema v1

Status: agreed Group A storage contract; NOT an extractor implementation. 2026-10-18.

## 14 agreed Group A fields

| # | Feature | Database storage |
|---|---|---|
| 1 | Video duration, duration_sec | video_feature_a_analyses.duration_sec |
| 2 | Aspect ratio, aspect_ratio | video_feature_a_analyses.aspect_ratio |
| 3 | Scene count, scene_count | video_feature_a_summary, calculated from scene rows |
| 4 | Every scene start/end/duration | video_feature_a_scenes; duration=end_sec-start_sec |
| 5 | First on-screen text time, first_text_sec | computed from screen-text spans |
| 6 | Spoken word count, spoken_words | video_feature_a_analyses.spoken_words |
| 7 | Speech words per minute, speech_wpm | computed from spoken_words / active speech minutes |
| 8 | Has subtitles, has_subtitles | true / false / unknown |
| 9 | Speech time coverage percentage | computed from speech_duration_sec/duration_sec*100 |
| 10 | Start of CTA, cta_start_sec | video_feature_a_analyses.cta_start_sec |
| 11 | Transcript segments with timestamps | video_feature_a_speech_segments |
| 12 | All on-screen text with timestamps | video_feature_a_screen_text |
| 13 | First speech time, first_speech_sec | computed from transcript segment start times |
| 14 | Music and SFX timestamp events | video_feature_a_audio_events (optional / lower priority) |

Explicitly REMOVED from Group A: average scene length (avg_scene_sec),
resolution (width_px and height_px), and frame rate (fps).

## Data provenance and relationships

- All records link to the existing canonical videos.id; no secondary video catalog.
- Optional media_asset_id is linked to the matching video and authorized source.
- Source media rights remain opt-in. INSERT/UPDATE of a feature analysis is
  refused for disabled or unauthorized sources. Manual D1 metadata-only imports
  do not automatically gain permission to analyze media.
- analysis_key is the idempotent observation key; analysis_version, method_name,
  method_version, model_name, model_version and measured_at allow later
  reprocessing without rewriting older analyses.
- measurement_methods and unavailable_reasons are typed JSON objects for
  method details and unknown values. Per-stream statuses distinguish not_run,
  complete, partial and unavailable.
- A scene is a measured interval, not its semantic interpretation. Labels
  like hook/problem/solution belong to Group B. CTA timestamp may require
  classification by B and must retain supporting cta_evidence.
- No average scene duration is stored. Duration of each scene derives from
  its individual start and end times; count derives from actual scene rows.
- WPM divides spoken_words by measured active speech duration (minutes), not
  full video length. The future extractor must calculate the union of
  overlapping speech intervals to avoid double-counting.
- Missing data stays NULL/unknown. Division by zero produces NULL; measured
  zero values remain distinct from fields that were never measured.

## What migration 003 DOES and DOES NOT do

The additive migration creates Group A tables, indexes, simple sanity checks,
a calculated view and a rights-validation trigger. It does NOT call video
platforms, scrape, run FFmpeg/ASR/OCR/AI, enqueue jobs or change downloader
behavior. The existing videos, media_assets and metric_snapshots remain intact.

Future processor requirements: enforce rights/retention at both read and write;
validate ordering and nonoverlap of scene spans and that all timestamps are
within duration_sec; normalize word counting and active speech duration;
provide strict versioned JSON contracts; separately approve schemas for B/C.

This is the storage foundation, not a claim that 14 features are already
measured by ContentMachine.

## Update: executing stage A1 in isolated feature branch (2026-10-23)

This historical document describes the original 003 schema and remains unchanged in its original commitments. An executable **partial technical extractor** has now been implemented in branch `feature/group-a-technical-extraction-v1-20261023`, above unmerged QDP. See [GROUP_A_EXECUTION_STAGE_A1.md](GROUP_A_EXECUTION_STAGE_A1.md). Only duration, aspect ratio, scene_count and technical scene intervals are measured. All other A features remain not_run/unknown and are not claimed complete. No automatic paid calls or provider media fetching.
