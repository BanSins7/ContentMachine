# ContentMachine — Group A: photo and mixed carousels

Status: database foundation v1, 2026-10-18. This is **not** media scraping or an AI analyzer.

## Content kinds and identity

All publications keep their existing canonical videos.id and unique platform/platform_video_id,
including TikTok photo posts and Instagram posts. The historical table name videos is kept for
backward compatibility; its rows now represent media publications in general.

The five agreed kinds are:

- video — a video file, uses Group A migration 003;
- slideshow_video — photo-like frames played automatically as a video; still Group A 003, with scene timestamps;
- photo_carousel — two or more static image slides, uses Group A migration 004;
- mixed_carousel — at least one image and one video slide, uses migration 004 and optionally links video slides to A003;
- image_post — a single static image, uses migration 004.

videos.content_kind already existed and is nullable. A URL-only D1 import does not prove the
number of slides or media types, and therefore MUST leave the kind unknown unless trustworthy
media observations are available. This migration does not rewrite historical rows or modify
the existing nullable column. Each carousel analysis also stores its independently observed kind.
When videos.content_kind is populated, the carousel analysis enforces that it agrees.

## Physical database model

- carousel_feature_a_analyses — analysis/version, publication id, source and source policy,
  classified media kind, analysis/measurement status, optional first CTA slide, unknown reasons,
  audio presence and details. A new analysis_key denotes another attempt; completed analyses
  are frozen rather than overwritten.
- carousel_feature_a_items — one row per slide, with slide_index (1-based), item_type (image/video),
  per-slide aspect_ratio, optional authorized media_asset_id, on_screen_text, screen_word_count,
  OCR method/confidence, and video-only optional duration_sec and video_a_analysis_id.
- carousel_feature_a_summary — computed slide_count, first_text_slide and total_screen_word_count;
  the latter two are only definitive when text coverage is complete.
- video_feature_a_analyses — existing video analyzer data, reusable for a VIDEO slide via the
  optional video_a_analysis_id; it keeps actual scene, speech, subtitle and audio timestamps
  relative to that video segment.

For an image slide, audio, speech, scene and playback durations are not fabricated.
An optional audio track for the publication is represented by has_audio and audio_details,
**not** a fictional time spent swiping. Any confirmed video slide uses its own measured duration.
CTA on a carousel is the slide number, not a timestamp; deciding that content is a CTA
is still Group B/classification, and evidence belongs in cta_evidence.

The 18-row unified Group A contract is: kind, duration where applicable, item count,
scene timeline / slide order, aspect ratio, on-screen text, first text position,
screen word count, spoken word count when applicable, WPM when applicable,
timestamped transcript when applicable, first speech when applicable, subtitles when
applicable, speech coverage when applicable, CTA position, audio events when applicable,
item_type, source media reference. Mean scene duration, resolution and FPS stay excluded.

## Validations and safeguards

The database rejects unsupported carousel kinds, duplicate/nonpositive slide indices,
image slides with fabricated durations, invalid OCR confidence, and incomplete OCR falsely
marked complete. Completing an analysis checks continuous slide indices, count/types
appropriate for image_post/photo_carousel/mixed_carousel, CTA index range and
complete coverage if global OCR was declared complete.

Media analysis and slide updates require sources.can_analyze_media=true and status=enabled;
linked media must match this publication and source, be non-metadata-only and unexpired.
The source policy version, when supplied, must belong to the same source.
Any linked video A003 analysis must match the same publication/source and media.
Completed analyses cannot be changed: a new version is required for corrections.
The existing D1 local manual metadata source remains can_analyze_media=false.

As in migration 003, future production processors must enforce retention and revocation
during reads too. This initial DDL is a preparation step, not a data-processing service.
There is no OCR, downloaded image, web crawler, automatic classification, CTA recognizer
or video generator in this change.

## D1 manual URL acceptance

D1 manual import now recognizes canonical TikTok /@username/photo/ID links, alongside
the existing TikTok /video/, Instagram /p/ and /reel/, and YouTube links. It saves
metadata-only references without requesting external media or guessing content_kind.
Actual slide enumeration and analysis need a separate permitted extractor in a later stage.
