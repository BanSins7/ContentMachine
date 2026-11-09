-- Independent Marketing Group A analysis v3. Historical immutable v1/v2 remain readable.
-- No QDP, Group A external tables, or other pipeline foreign keys.
ALTER TABLE mi_video_analyses
 DROP CONSTRAINT IF EXISTS mi_video_analyses_method_version_check;
ALTER TABLE mi_video_analyses
 ADD CONSTRAINT mi_video_analyses_method_version_check
 CHECK (method_version IN (
   'mi-independent-media/v1',
   'mi-abc-expanded-media/v2',
   'mi-abc-audio-expanded-media/v3'
 ));
