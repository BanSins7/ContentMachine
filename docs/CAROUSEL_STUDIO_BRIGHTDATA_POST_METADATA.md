# Carousel Studio: Bright Data post metadata

The source branch now persists **description**, **hashtags**, **music** and
**original_sound** for TikTok photo carousels when those fields are available
in the Bright Data profile or post-detail snapshot.

- Post detail values take precedence; missing fields use the profile snapshot.
- Descriptions preserve the original UTF-8 text including spaces and hashtags.
- Hashtags preserve the provider's actual value (string, array or JSON object).
- Music preserves the **complete provider-returned structured value**; no assumed
  internal music schema, artist or song names are invented.
- Original sound preserves its actual boolean, string or structured value.
- Missing or unsupported values become `null`, never fabricated defaults.
- A per-field bound of 200KB prevents huge non-serializable responses.
- No additional Bright Data triggers or LLM calls are introduced.

Persistence paths: `studio_items.metrics_json.observed_post_metadata`,
per-carousel `metrics.json`, exported `full-analysis.json`, ZIP, and
`studio_channel_posts.counts_json.observed_post_metadata` for profile-photo
carousels. The latter survives an interrupted run and is available for resume.
The run detail API also exposes each item's `post_metadata` field.

**Existing completed runs are left untouched.** If an old result lacked the
fields, they can only be added from an available saved snapshot or a new
explicit provider collection. This patch never silently rebills providers.
