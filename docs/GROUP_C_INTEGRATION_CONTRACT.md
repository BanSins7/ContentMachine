# Group C — Integration Boundary v0
Status: bootstrap boundary only. Does NOT implement real QDP consumer, metric-scoring worker or Analytics publisher.

## Ownership and canonical identity
- D1 videos.id is the only publication ID; source_id TEXT and project_id 32 lower-hex. QDP UUID identifies a preparation run, not a publication ID.
- metric_snapshots is canonical append-only C0 fact storage; metric snapshot ID and exact source_metric_definition + source_metric_version are mandatory.
- Metadata published_at must be tied to source_video_metadata.id; creation/discovery time cannot substitute it.
- Metric config IDs, frozen comparison cohort IDs and human approval evidence are mandatory for C7 complete.
- Group C uses calculated_metrics already defined by 003/004; it must not duplicate the table or rewrite existing applied SQL.

## Incoming ports
- QDP: C readiness/preparation pointer, exact project/video/source/snapshot SHA; durable inbox ACK only after transaction.
- D1 metric reader: real views, interactions, observed_at, definition/version, quality, expiration, prior observation selection. No source-provider requests inside C.
- Cohort builder: creator/niche/engagement_prior peers are compatible, frozen, and exclude target post; min sample reviewed.
- Config approver: alpha>0, Bayesian k>0, prior rates, provenance, 48h half-life, immutable C7 weights must be reviewed; unknown leaves pending.

## Outgoing port
- To Unified Analytics: persisted calculated_metrics ID, snapshot & previous IDs, publication metadata, config/cohort IDs, score_status, four component contributions and source-policy version.
- Analytics PR23 currently contains a pure C scorer; after cross-branch integration it must delegate to Group C rather than silently maintain competing scoring implementations.

## Mathematical constraints
C1=(V+alpha)/(creator_median+alpha); C2 niche optional; C3=(V2-V1)/elapsed_hours; C4 raw engagement = (L+C+S)/V and weighted=(L+1.5C+3S)/V; C5 smoothed Bayesian components; C6 freshness 48-hour half-life; C7=.45*C1+.30*C3+.15*C5+.10*C6.
Breakout Score is NOT normalized or bounded by 100; niche does not contribute. Saves not included in weighted L/C/S. Unknown values NULL, never fabricated 0.

## Source/revision semantics
Same post/source/platform/content type/source_metric_definition+version; increasing actual observation timestamps; publication_at anchored. No self in cohort peers, no stale/unapproved prior or method.
Current source-policy/retention/expiry checked at calculation AND read; corrected/retracted observations invalidate current readable score.

## Integration status
QDP, VC, Analytics remain independently developed; version compatibility must be reverified on each stage, typed unavailable until then.