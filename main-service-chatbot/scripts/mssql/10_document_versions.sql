-- Knowledge-base document versions (app/logic/versions.py). Forward-only and re-runnable.
--
-- A document is a `document_key` (the file name without extension or version tag, lower-cased). Each upload
-- with that key is the next `version_no`. The newest ingested version is `is_latest`; older ones stay
-- searchable, but only when a question asks for them. Rows from before this script have a NULL key until
-- POST /admin/versions/sync assigns keys, numbers their versions and elects the latest.

IF COL_LENGTH('dbo.files', 'document_key') IS NULL
  ALTER TABLE dbo.files ADD document_key NVARCHAR(255) NULL;
GO
IF COL_LENGTH('dbo.files', 'version_no') IS NULL
  ALTER TABLE dbo.files ADD version_no INT NOT NULL CONSTRAINT df_files_version_no DEFAULT 1;
GO
IF COL_LENGTH('dbo.files', 'is_latest') IS NULL
  ALTER TABLE dbo.files ADD is_latest BIT NOT NULL CONSTRAINT df_files_is_latest DEFAULT 1;
GO
IF COL_LENGTH('dbo.files', 'version_label') IS NULL
  ALTER TABLE dbo.files ADD version_label NVARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.files', 'effective_date') IS NULL
  ALTER TABLE dbo.files ADD effective_date DATE NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_files_document_key' AND object_id = OBJECT_ID('dbo.files'))
  CREATE INDEX ix_files_document_key ON dbo.files (document_key, version_no);
GO

DROP PROCEDURE IF EXISTS dbo.filter_files;
GO
DROP PROCEDURE IF EXISTS dbo.next_document_version;
GO
DROP PROCEDURE IF EXISTS dbo.set_file_version;
GO
DROP PROCEDURE IF EXISTS dbo.promote_latest_version;
GO
DROP PROCEDURE IF EXISTS dbo.list_document_versions;
GO

-- 01's filter_files, plus the version columns.
CREATE PROCEDURE dbo.filter_files(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_mimetype NVARCHAR(100),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit INT = COALESCE(@p_page_size, 10);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT id, name, mimetype, file_path, parsed_path, kb_file_id,
         processing_status, processing_error, is_active,
         document_key, version_no, is_latest, version_label, effective_date,
         created_by, created_on, updated_by, updated_on
  FROM dbo.files
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_name IS NULL OR name LIKE '%' + @p_name + '%')
    AND (@p_mimetype IS NULL OR mimetype = @p_mimetype)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;
END
GO

-- [document_key]: the version number the next upload of this document gets. Counts inactive rows too, so a
-- number is never reused after a delete.
CREATE PROCEDURE dbo.next_document_version(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  SELECT COALESCE(MAX(version_no), 0) + 1 AS version_no
  FROM dbo.files
  WHERE document_key = @p_document_key;
END
GO

-- [id, document_key, version_no, version_label, effective_date]: stamps a file record with its version. It is
-- not the latest until promote_latest_version says so, once it has been ingested.
CREATE PROCEDURE dbo.set_file_version(
  @p_id INT,
  @p_document_key NVARCHAR(255),
  @p_version_no INT,
  @p_version_label NVARCHAR(50),
  @p_effective_date DATE
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files
  SET document_key = @p_document_key,
      version_no = @p_version_no,
      is_latest = 0,
      version_label = @p_version_label,
      effective_date = @p_effective_date
  WHERE id = @p_id;
END
GO

-- [document_key]: elects the latest version - the highest version_no among the active, ingested ones - and
-- returns every such version with its flag, which the caller mirrors onto the vector store's chunks. Returning
-- the whole group (not only what changed) makes a retry after a failed mirror correct.
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

  SELECT id, kb_file_id, version_no, is_latest
  FROM dbo.files
  WHERE document_key = @p_document_key AND is_active = 1 AND kb_file_id IS NOT NULL
  ORDER BY version_no DESC;
END
GO

-- [document_key]: every version of a document, newest first, including deleted ones.
CREATE PROCEDURE dbo.list_document_versions(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, name, version_no, version_label, effective_date, is_latest, is_active,
         processing_status, kb_file_id, created_by, created_on
  FROM dbo.files
  WHERE document_key = @p_document_key
  ORDER BY version_no DESC, id DESC;
END
GO
