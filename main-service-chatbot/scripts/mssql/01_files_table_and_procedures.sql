-- T-SQL port of ../files_table_and_procedures.sql, ../files_knowledge_base_column.sql,
-- ../add_file_processing_status.sql and ../remove_file_type_column.sql, folded into
-- final-state schema: MSSQL is a new backend with no prior deployment to carry
-- forward, so there is no MySQL migration history to replay here - just the
-- table and procedures as they exist today. See services/database_factory.py
-- and services/mssql_service.py for how this is selected and called.

IF OBJECT_ID('dbo.files', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.files (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(255) NOT NULL,
    mimetype NVARCHAR(100) NOT NULL,
    file_path NVARCHAR(1000) NOT NULL,
    parsed_path NVARCHAR(1000) NULL,
    kb_file_id NVARCHAR(255) NULL,
    processing_status NVARCHAR(20) NOT NULL DEFAULT 'pending',
    processing_error NVARCHAR(1000) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_by NVARCHAR(255) NOT NULL,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by NVARCHAR(255) NULL,
    updated_on DATETIME2 NULL
  );
END
GO

DROP PROCEDURE IF EXISTS dbo.upsert_file;
GO
DROP PROCEDURE IF EXISTS dbo.filter_files;
GO
DROP PROCEDURE IF EXISTS dbo.delete_file;
GO
DROP PROCEDURE IF EXISTS dbo.update_file_processing_status;
GO

CREATE PROCEDURE dbo.upsert_file(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_mimetype NVARCHAR(100),
  @p_file_path NVARCHAR(1000),
  @p_parsed_path NVARCHAR(1000),
  @p_is_active BIT,
  @p_kb_file_id NVARCHAR(255),
  @p_updated_by NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.files (name, mimetype, file_path, parsed_path, is_active, kb_file_id, created_by, created_on)
    VALUES (@p_name, @p_mimetype, @p_file_path, @p_parsed_path, COALESCE(@p_is_active, 1), @p_kb_file_id, @p_updated_by, SYSUTCDATETIME());
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.files
    SET name = @p_name,
        mimetype = @p_mimetype,
        file_path = @p_file_path,
        parsed_path = @p_parsed_path,
        is_active = @p_is_active,
        kb_file_id = @p_kb_file_id,
        updated_by = @p_updated_by,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

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

CREATE PROCEDURE dbo.delete_file(
  @p_id INT,
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files
  SET is_active = 0,
      updated_by = @p_actor,
      updated_on = SYSUTCDATETIME()
  WHERE id = @p_id;
END
GO

-- A lightweight, standalone status update - deliberately not folded into
-- upsert_file, so kb_worker can flip status without needing to know (and
-- risk overwriting) the rest of the row.
CREATE PROCEDURE dbo.update_file_processing_status(
  @p_id INT,
  @p_processing_status NVARCHAR(20),
  @p_processing_error NVARCHAR(1000)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files
  SET processing_status = @p_processing_status,
      processing_error = @p_processing_error,
      updated_on = SYSUTCDATETIME()
  WHERE id = @p_id;
END
GO
