-- Two concerns in one migration (safe to re-run):
--
-- 1. DMS document tables (dms_category, dms_document, dms_document_version,
--    dms_document_comments): mirrors of the DMS service schema, added here so the
--    chatbot can read document metadata and serve PVC-mounted files to clients.
--    `blob_uri` is NOT carried over - the equivalent column is `pvc_path`, a
--    store-relative path ("chatbot-kb/abc123_report.pdf") that the local
--    FileStorageService maps onto the mounted PVC.  If a table already exists and
--    has `blob_uri` but not `pvc_path`, the column is added automatically.
--
-- 2. Tenant context on chatbot_session: upsert_chatbot_session now stores
--    entity_id / tenant_id / user_id / user_type / is_admin that come from the
--    caller's identity.  The columns already exist in chatbot_session (added by
--    14_unified_schema.sql); this script only updates the stored procedure so it
--    actually writes them instead of keeping the default demo values.
--    Controlled by TENANT_CONTEXT_ENABLED in the Python service; when False the
--    caller still passes these params but they carry the same demo GUIDs as before,
--    so old behaviour is preserved.

-- ============================================================
-- 1. DMS tables
-- ============================================================

-- dms_category - must exist before dms_document (FK source)
IF OBJECT_ID('dbo.dms_category', 'U') IS NULL
  CREATE TABLE dbo.dms_category (
    id           INT           IDENTITY(1,1) NOT NULL PRIMARY KEY,
    name         NVARCHAR(200) NOT NULL,
    code         NVARCHAR(100) NULL,
    description  NVARCHAR(MAX) NULL,
    is_archived  BIT           NOT NULL DEFAULT 0,
    created_by   NVARCHAR(255) NULL,
    creation_date DATETIME     NULL DEFAULT GETUTCDATE(),
    updated_by   NVARCHAR(255) NULL,
    date_updated DATETIME      NULL
  );
GO

-- dms_document_version (created before dms_document because dms_document
-- has a FK to it via current_version_id)
IF OBJECT_ID('dbo.dms_document_version', 'U') IS NULL
  CREATE TABLE dbo.dms_document_version (
    id                   BIGINT         IDENTITY(1,1) NOT NULL PRIMARY KEY,
    dms_document_id      BIGINT         NOT NULL,
    version_number       DECIMAL(6,2)   NOT NULL,
    status               NVARCHAR(40)   NOT NULL,
    effective_from       DATETIME       NULL,
    effective_to         DATETIME       NULL,
    markdown             NVARCHAR(MAX)  NULL,
    -- pvc_path replaces blob_uri: a store-relative path the FileStorageService maps
    -- onto the mounted PVC ("chatbot-kb/<filename>").  See storage_service.py.
    pvc_path             NVARCHAR(1000) NULL,
    document_source_type NVARCHAR(50)   NOT NULL DEFAULT 'upload',
    hash                 VARBINARY(64)  NULL,
    is_archived          BIT            NOT NULL DEFAULT 0,
    creation_date        DATETIME       NULL DEFAULT GETUTCDATE(),
    created_by           NVARCHAR(MAX)  NULL,
    date_updated         DATETIME       NULL,
    updated_by           NVARCHAR(MAX)  NULL
  );
GO

-- If the table already existed with blob_uri, add pvc_path next to it.
IF COL_LENGTH('dbo.dms_document_version', 'pvc_path') IS NULL
  ALTER TABLE dbo.dms_document_version ADD pvc_path NVARCHAR(1000) NULL;
GO

-- dms_document
IF OBJECT_ID('dbo.dms_document', 'U') IS NULL
  CREATE TABLE dbo.dms_document (
    id                  BIGINT          IDENTITY(1,1) NOT NULL PRIMARY KEY,
    dms_category_id     INT             NOT NULL,
    name                NVARCHAR(200)   NOT NULL,
    code                NVARCHAR(100)   NULL,
    description         NVARCHAR(MAX)   NULL,
    title               NVARCHAR(500)   NOT NULL,
    icon                NVARCHAR(1000)  NULL,
    current_version_id  BIGINT          NULL,
    sensitivity         NVARCHAR(50)    NULL,
    is_archived         BIT             NOT NULL DEFAULT 0,
    creation_date       DATETIME        NULL DEFAULT GETUTCDATE(),
    created_by          NVARCHAR(MAX)   NULL,
    date_updated        DATETIME        NULL,
    updated_by          NVARCHAR(MAX)   NULL,
    -- status stored as int (lookup value) - no FK to option_value to avoid a
    -- cross-service dependency; the DMS service owns that table.
    status              INT             NULL,
    CONSTRAINT fk_dms_document_dms_category_id
      FOREIGN KEY (dms_category_id) REFERENCES dbo.dms_category (id),
    CONSTRAINT fk_dms_document_current_version_id
      FOREIGN KEY (current_version_id) REFERENCES dbo.dms_document_version (id)
  );
GO

-- Ensure the FK from dms_document_version back to dms_document exists.
-- Can only be added now that dms_document exists.
IF NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys
  WHERE name = 'fk_dms_document_version_dms_document_id'
    AND parent_object_id = OBJECT_ID('dbo.dms_document_version')
)
  ALTER TABLE dbo.dms_document_version
    ADD CONSTRAINT fk_dms_document_version_dms_document_id
    FOREIGN KEY (dms_document_id) REFERENCES dbo.dms_document (id);
GO

-- dms_document_comments
IF OBJECT_ID('dbo.dms_document_comments', 'U') IS NULL
  CREATE TABLE dbo.dms_document_comments (
    id              BIGINT         IDENTITY(1,1) NOT NULL PRIMARY KEY,
    dms_document_id BIGINT         NOT NULL,
    comment         NVARCHAR(MAX)  NULL,
    creation_date   DATETIME       NULL DEFAULT GETUTCDATE(),
    created_by      NVARCHAR(MAX)  NULL,
    date_updated    DATETIME       NULL,
    updated_by      NVARCHAR(MAX)  NULL,
    CONSTRAINT fk_dms_document_comments_dms_document_id
      FOREIGN KEY (dms_document_id) REFERENCES dbo.dms_document (id)
  );
GO

-- ============================================================
-- 2. DMS stored procedures
-- ============================================================

DROP PROCEDURE IF EXISTS dbo.filter_dms_documents;
GO
DROP PROCEDURE IF EXISTS dbo.filter_dms_document_versions;
GO
DROP PROCEDURE IF EXISTS dbo.get_dms_document_version;
GO

-- List documents (optionally filtered by category, archived state)
CREATE PROCEDURE dbo.filter_dms_documents(
  @p_id              BIGINT,
  @p_dms_category_id INT,
  @p_name            NVARCHAR(200),
  @p_is_archived     BIT,
  @p_page_number     INT,
  @p_page_size       INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 20);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT d.id, d.dms_category_id, d.name, d.code, d.title, d.description,
         d.icon, d.current_version_id, d.sensitivity, d.is_archived,
         d.status, d.creation_date, d.created_by, d.date_updated, d.updated_by,
         v.pvc_path AS current_pvc_path, v.version_number AS current_version_number
  FROM dbo.dms_document d
  LEFT JOIN dbo.dms_document_version v ON v.id = d.current_version_id
  WHERE (@p_id              IS NULL OR d.id              = @p_id)
    AND (@p_dms_category_id IS NULL OR d.dms_category_id = @p_dms_category_id)
    AND (@p_name            IS NULL OR d.name            LIKE '%' + @p_name + '%')
    AND (@p_is_archived     IS NULL OR d.is_archived     = @p_is_archived)
  ORDER BY d.creation_date DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;

  SELECT COUNT(*) AS total_records
  FROM dbo.dms_document d
  WHERE (@p_id              IS NULL OR d.id              = @p_id)
    AND (@p_dms_category_id IS NULL OR d.dms_category_id = @p_dms_category_id)
    AND (@p_name            IS NULL OR d.name            LIKE '%' + @p_name + '%')
    AND (@p_is_archived     IS NULL OR d.is_archived     = @p_is_archived);
END
GO

-- List all versions of a document
CREATE PROCEDURE dbo.filter_dms_document_versions(
  @p_dms_document_id BIGINT,
  @p_is_archived     BIT
)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, dms_document_id, version_number, status, effective_from, effective_to,
         pvc_path, document_source_type, is_archived, creation_date, created_by,
         date_updated, updated_by
  FROM dbo.dms_document_version
  WHERE (@p_dms_document_id IS NULL OR dms_document_id = @p_dms_document_id)
    AND (@p_is_archived     IS NULL OR is_archived     = @p_is_archived)
  ORDER BY version_number DESC;
END
GO

-- Fetch one version by ID (used when serving the file for download)
CREATE PROCEDURE dbo.get_dms_document_version(@p_id BIGINT)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT v.id, v.dms_document_id, v.version_number, v.status,
         v.effective_from, v.effective_to, v.pvc_path, v.markdown,
         v.document_source_type, v.is_archived, v.creation_date, v.created_by,
         d.name AS document_name, d.title AS document_title,
         d.dms_category_id, d.sensitivity
  FROM dbo.dms_document_version v
  JOIN dbo.dms_document d ON d.id = v.dms_document_id
  WHERE v.id = @p_id;
END
GO

-- ============================================================
-- 3. Update upsert_chatbot_session to store tenant context
-- ============================================================

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_session;
GO

CREATE PROCEDURE dbo.upsert_chatbot_session(
  @p_id        NVARCHAR(36),
  @p_name      NVARCHAR(255),
  @p_is_active BIT,
  @p_actor     NVARCHAR(255),
  -- Tenant / identity context (written on INSERT only; never overwritten on UPDATE
  -- so a session's origin is immutable once created).
  @p_entity_id  NVARCHAR(36)  = NULL,
  @p_tenant_id  NVARCHAR(36)  = NULL,
  @p_user_id    NVARCHAR(36)  = NULL,
  @p_user_type  NVARCHAR(50)  = NULL,
  @p_is_admin   BIT           = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_session_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_session
      (id, name, is_active, entity_id, tenant_id, user_id, user_type, is_admin, created_by, created_on)
    VALUES
      (@new_session_id, @p_name, COALESCE(@p_is_active,1),
       COALESCE(@p_entity_id,  '11111111-1111-1111-1111-111111111111'),
       COALESCE(@p_tenant_id,  '22222222-2222-2222-2222-222222222222'),
       COALESCE(@p_user_id,    '33333333-3333-3333-3333-333333333333'),
       COALESCE(@p_user_type,  'demo_user'),
       COALESCE(@p_is_admin,   0),
       @p_actor, SYSUTCDATETIME())
    SELECT @new_session_id AS id
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_session
    SET name       = COALESCE(@p_name, name),
        is_active  = COALESCE(@p_is_active, is_active),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id
    SELECT @p_id AS id
  END
END
GO
