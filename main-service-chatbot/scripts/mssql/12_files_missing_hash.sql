-- Files uploaded before content hashing (09) have no content_hash, so a byte-identical re-upload of one of them
-- is not recognised as a duplicate. This lists the active ones so POST /admin/files/backfill-hashes can hash them.
-- Forward-only and re-runnable.

DROP PROCEDURE IF EXISTS dbo.list_files_missing_hash;
GO

CREATE PROCEDURE dbo.list_files_missing_hash
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, name, file_path
  FROM dbo.files
  WHERE is_active = 1 AND content_hash IS NULL
  ORDER BY id;
END
GO
