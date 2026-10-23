-- list_stuck_files: returns active files whose parse+index job has not progressed
-- past 'pending' or 'processing' within @p_stuck_seconds seconds of the last status
-- change (or their creation time when the status was never updated).
-- Used by the startup sweep and the periodic watchdog in app/logic/ingest_recovery.py.
-- Safe to re-run.

DROP PROCEDURE IF EXISTS dbo.list_stuck_files;
GO

CREATE PROCEDURE dbo.list_stuck_files(@p_stuck_seconds INT)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, name, mimetype, file_path, extracted_text_path, vector_store_file_id,
         processing_status, processing_error, is_active,
         document_key, version_number, is_latest, version_label, effective_date,
         created_by, created_on, updated_by, updated_on
  FROM dbo.files
  WHERE is_active = 1
    AND processing_status IN ('pending', 'processing')
    AND DATEDIFF(SECOND, COALESCE(updated_on, created_on), SYSUTCDATETIME()) > @p_stuck_seconds;
END
GO
