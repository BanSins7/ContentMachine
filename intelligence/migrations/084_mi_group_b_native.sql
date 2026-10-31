-- Marketing Group B independent eight-stage versioned analytics.
-- This adds no Group B / QDP foreign keys or cross-pipeline dependencies.
ALTER TABLE mi_video_analyses
 DROP CONSTRAINT IF EXISTS mi_video_analyses_method_version_check;
ALTER TABLE mi_video_analyses
 ADD CONSTRAINT mi_video_analyses_method_version_check
 CHECK (method_version IN (
   'mi-independent-media/v1',
   'mi-abc-expanded-media/v2',
   'mi-abc-audio-expanded-media/v3',
   'mi-abc-b8-native-media/v4'
 ));
