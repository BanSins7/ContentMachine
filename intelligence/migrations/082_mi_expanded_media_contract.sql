-- MI native media analytics v2 extends independent versioned observations.
-- Group A/B/C source algorithms are copied into MI; no QDP foreign keys
-- or dependencies are added to Marketing. Prior v1 observations remain immutable.
ALTER TABLE mi_video_analyses
 DROP CONSTRAINT IF EXISTS mi_video_analyses_method_version_check;
ALTER TABLE mi_video_analyses
 ADD CONSTRAINT mi_video_analyses_method_version_check
 CHECK (method_version IN ('mi-independent-media/v1','mi-abc-expanded-media/v2'));
