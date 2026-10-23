-- Follow-up to 10_document_versions.sql (forward-only: 10 has already been applied, so it is not edited).
--
-- 1. assign_file_version: numbers a new version and stamps the file record in ONE locked step. 10's
--    next_document_version followed by set_file_version left a gap in which two uploads of the same
--    document (two admins, two requests) read the same "next" number and both became version 2.
-- 2. promote_latest_version also returns each version's label and document key, so the caller can write the
--    complete version onto the chunks (files from before versioning have chunks with none of it).

DROP PROCEDURE IF EXISTS dbo.assign_file_version;
GO
DROP PROCEDURE IF EXISTS dbo.promote_latest_version;
GO

-- [id, document_key, version_label, effective_date] -> one row: version_no. Holds a key-range lock on the
-- document's rows while it counts and writes, so concurrent uploads of one document get distinct numbers. Counts
-- inactive rows too, so a number is never reused after a delete. The file is not the latest until
-- promote_latest_version says so, once it has been ingested.
CREATE PROCEDURE dbo.assign_file_version(
  @p_id INT,
  @p_document_key NVARCHAR(255),
  @p_version_label NVARCHAR(50),
  @p_effective_date DATE
)
AS
BEGIN
  SET NOCOUNT ON;
  SET XACT_ABORT ON;
  DECLARE @v_no INT;
  BEGIN TRANSACTION;
    SELECT @v_no = COALESCE(MAX(version_no), 0) + 1
    FROM dbo.files WITH (UPDLOCK, HOLDLOCK)
    WHERE document_key = @p_document_key;

    UPDATE dbo.files
    SET document_key = @p_document_key,
        version_no = @v_no,
        is_latest = 0,
        version_label = @p_version_label,
        effective_date = @p_effective_date
    WHERE id = @p_id;
  COMMIT TRANSACTION;
  SELECT @v_no AS version_no;
END
GO

-- [document_key]: elects the latest version - the highest version_no among the active, ingested ones - and
-- returns every such version with its flag and version fields, which the caller mirrors onto the vector store's
-- chunks. Returning the whole group (not only what changed) makes a retry after a failed mirror correct.
CREATE PROCEDURE dbo.promote_latest_version(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  ;WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY version_no DESC, id DESC) AS rn
    FROM dbo.files
    WHERE document_key = @p_document_key AND is_active = 1 AND kb_file_id IS NOT NULL
  )
  UPDATE f
  SET is_latest = CASE WHEN r.rn = 1 THEN 1 ELSE 0 END
  FROM dbo.files f
  JOIN ranked r ON r.id = f.id;

  UPDATE dbo.files
  SET is_latest = 0
  WHERE document_key = @p_document_key AND (is_active = 0 OR kb_file_id IS NULL);

  SELECT id, kb_file_id, document_key, version_no, version_label, is_latest
  FROM dbo.files
  WHERE document_key = @p_document_key AND is_active = 1 AND kb_file_id IS NOT NULL
  ORDER BY version_no DESC;
END
GO
