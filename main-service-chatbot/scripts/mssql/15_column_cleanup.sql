-- Schema cleanup: rename opaque columns and drop dead ones.
--
-- Renames:
--   files.kb_file_id            -> vector_store_file_id
--   files.parsed_path           -> extracted_text_path
--   files.version_no            -> version_number
--   chatbot_history.retrieved_collections -> rag_collections
--   audit_log.outcome           -> chat_outcome
--   audit_log.top_score         -> top_retrieval_score
--
-- Drops (always-NULL, orphaned, or never written):
--   chatbot_session.owner_id     (dbo.users was dropped in 14; always NULL)
--   chatbot_history.owner_id     (same)
--   chatbot_history.feedback     (never written - goes to chatbot_feedback table instead)
--   chatbot_history.comment      (never written)
--   chatbot_feedback.owner_id    (same as above)
--
-- Safe to re-run.

-- ============================================================
-- 1. Drop stored procedures that reference changed columns
-- ============================================================

DROP PROCEDURE IF EXISTS dbo.upsert_file;
GO
DROP PROCEDURE IF EXISTS dbo.filter_files;
GO
DROP PROCEDURE IF EXISTS dbo.next_document_version;
GO
DROP PROCEDURE IF EXISTS dbo.set_file_version;
GO
DROP PROCEDURE IF EXISTS dbo.assign_file_version;
GO
DROP PROCEDURE IF EXISTS dbo.promote_latest_version;
GO
DROP PROCEDURE IF EXISTS dbo.list_document_versions;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_sessions;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_feedback;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_feedback;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.get_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.chat_metrics;
GO

-- ============================================================
-- 2. Drop dead columns (constraints and indexes first)
-- ============================================================

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_session_owner' AND object_id = OBJECT_ID('dbo.chatbot_session'))
  DROP INDEX ix_chatbot_session_owner ON dbo.chatbot_session;
GO

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_history_owner' AND object_id = OBJECT_ID('dbo.chatbot_history'))
  DROP INDEX ix_chatbot_history_owner ON dbo.chatbot_history;
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'ck_chatbot_history_feedback' AND parent_object_id = OBJECT_ID('dbo.chatbot_history'))
  ALTER TABLE dbo.chatbot_history DROP CONSTRAINT ck_chatbot_history_feedback;
GO

IF COL_LENGTH('dbo.chatbot_session', 'owner_id') IS NOT NULL
  ALTER TABLE dbo.chatbot_session DROP COLUMN owner_id;
GO

IF COL_LENGTH('dbo.chatbot_history', 'owner_id') IS NOT NULL
  ALTER TABLE dbo.chatbot_history DROP COLUMN owner_id;
GO
IF COL_LENGTH('dbo.chatbot_history', 'feedback') IS NOT NULL
  ALTER TABLE dbo.chatbot_history DROP COLUMN feedback;
GO
IF COL_LENGTH('dbo.chatbot_history', 'comment') IS NOT NULL
  ALTER TABLE dbo.chatbot_history DROP COLUMN comment;
GO

IF COL_LENGTH('dbo.chatbot_feedback', 'owner_id') IS NOT NULL
  ALTER TABLE dbo.chatbot_feedback DROP COLUMN owner_id;
GO

-- ============================================================
-- 3. Rename columns
-- ============================================================

IF COL_LENGTH('dbo.files', 'kb_file_id') IS NOT NULL
  EXEC sp_rename 'dbo.files.kb_file_id', 'vector_store_file_id', 'COLUMN';
GO
IF COL_LENGTH('dbo.files', 'parsed_path') IS NOT NULL
  EXEC sp_rename 'dbo.files.parsed_path', 'extracted_text_path', 'COLUMN';
GO
IF COL_LENGTH('dbo.files', 'version_no') IS NOT NULL
  EXEC sp_rename 'dbo.files.version_no', 'version_number', 'COLUMN';
GO

IF COL_LENGTH('dbo.chatbot_history', 'retrieved_collections') IS NOT NULL
  EXEC sp_rename 'dbo.chatbot_history.retrieved_collections', 'rag_collections', 'COLUMN';
GO

IF COL_LENGTH('dbo.audit_log', 'outcome') IS NOT NULL
  EXEC sp_rename 'dbo.audit_log.outcome', 'chat_outcome', 'COLUMN';
GO
IF COL_LENGTH('dbo.audit_log', 'top_score') IS NOT NULL
  EXEC sp_rename 'dbo.audit_log.top_score', 'top_retrieval_score', 'COLUMN';
GO

-- ============================================================
-- 4. Recreate stored procedures
-- ============================================================

-- files --

CREATE PROCEDURE dbo.upsert_file(
  @p_id                   INT,
  @p_name                 NVARCHAR(255),
  @p_mimetype             NVARCHAR(100),
  @p_file_path            NVARCHAR(1000),
  @p_extracted_text_path  NVARCHAR(1000),
  @p_is_active            BIT,
  @p_vector_store_file_id NVARCHAR(255),
  @p_updated_by           NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.files
      (name, mimetype, file_path, extracted_text_path, is_active, vector_store_file_id, created_by, created_on)
    VALUES
      (@p_name, @p_mimetype, @p_file_path, @p_extracted_text_path,
       COALESCE(@p_is_active, 1), @p_vector_store_file_id, @p_updated_by, SYSUTCDATETIME());
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.files
    SET name                 = @p_name,
        mimetype             = @p_mimetype,
        file_path            = @p_file_path,
        extracted_text_path  = @p_extracted_text_path,
        is_active            = @p_is_active,
        vector_store_file_id = @p_vector_store_file_id,
        updated_by           = @p_updated_by,
        updated_on           = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

CREATE PROCEDURE dbo.filter_files(
  @p_id       INT,
  @p_name     NVARCHAR(255),
  @p_mimetype NVARCHAR(100),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size   INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 10);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT id, name, mimetype, file_path, extracted_text_path, vector_store_file_id,
         processing_status, processing_error, is_active,
         document_key, version_number, is_latest, version_label, effective_date,
         created_by, created_on, updated_by, updated_on
  FROM dbo.files
  WHERE (@p_id       IS NULL OR id       = @p_id)
    AND (@p_name     IS NULL OR name     LIKE '%' + @p_name + '%')
    AND (@p_mimetype IS NULL OR mimetype = @p_mimetype)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;
END
GO

CREATE PROCEDURE dbo.next_document_version(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  SELECT COALESCE(MAX(version_number), 0) + 1 AS version_number
  FROM dbo.files
  WHERE document_key = @p_document_key;
END
GO

CREATE PROCEDURE dbo.set_file_version(
  @p_id             INT,
  @p_document_key   NVARCHAR(255),
  @p_version_number INT,
  @p_version_label  NVARCHAR(50),
  @p_effective_date DATE
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files
  SET document_key   = @p_document_key,
      version_number = @p_version_number,
      is_latest      = 0,
      version_label  = @p_version_label,
      effective_date = @p_effective_date
  WHERE id = @p_id;
END
GO

CREATE PROCEDURE dbo.assign_file_version(
  @p_id             INT,
  @p_document_key   NVARCHAR(255),
  @p_version_label  NVARCHAR(50),
  @p_effective_date DATE
)
AS
BEGIN
  SET NOCOUNT ON;
  SET XACT_ABORT ON;
  DECLARE @v_no INT;
  BEGIN TRANSACTION;
    SELECT @v_no = COALESCE(MAX(version_number), 0) + 1
    FROM dbo.files WITH (UPDLOCK, HOLDLOCK)
    WHERE document_key = @p_document_key;

    UPDATE dbo.files
    SET document_key   = @p_document_key,
        version_number = @v_no,
        is_latest      = 0,
        version_label  = @p_version_label,
        effective_date = @p_effective_date
    WHERE id = @p_id;
  COMMIT TRANSACTION;
  SELECT @v_no AS version_number;
END
GO

CREATE PROCEDURE dbo.promote_latest_version(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  ;WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY version_number DESC, id DESC) AS rn
    FROM dbo.files
    WHERE document_key = @p_document_key AND is_active = 1 AND vector_store_file_id IS NOT NULL
  )
  UPDATE f
  SET is_latest = CASE WHEN r.rn = 1 THEN 1 ELSE 0 END
  FROM dbo.files f
  JOIN ranked r ON r.id = f.id;

  UPDATE dbo.files
  SET is_latest = 0
  WHERE document_key = @p_document_key AND (is_active = 0 OR vector_store_file_id IS NULL);

  SELECT id, vector_store_file_id, document_key, version_number, version_label, is_latest
  FROM dbo.files
  WHERE document_key = @p_document_key AND is_active = 1 AND vector_store_file_id IS NOT NULL
  ORDER BY version_number DESC;
END
GO

CREATE PROCEDURE dbo.list_document_versions(@p_document_key NVARCHAR(255))
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, name, version_number, version_label, effective_date, is_latest, is_active,
         processing_status, vector_store_file_id, created_by, created_on
  FROM dbo.files
  WHERE document_key = @p_document_key
  ORDER BY version_number DESC, id DESC;
END
GO

-- chat session --

CREATE PROCEDURE dbo.upsert_chatbot_session(
  @p_id        NVARCHAR(36),
  @p_name      NVARCHAR(255),
  @p_is_active BIT,
  @p_actor     NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_session_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_session (id, name, is_active, created_by, created_on)
    VALUES (@new_session_id, @p_name, COALESCE(@p_is_active,1), @p_actor, SYSUTCDATETIME())
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

CREATE PROCEDURE dbo.filter_chatbot_sessions(
  @p_id          NVARCHAR(36),
  @p_name        NVARCHAR(255),
  @p_created_by  NVARCHAR(255),
  @p_is_active   BIT,
  @p_page_number INT,
  @p_page_size   INT
)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 10)
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit

  SELECT id, name, entity_id, tenant_id, user_id, user_type, is_admin,
         is_active, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_session
  WHERE (@p_id         IS NULL OR id         = @p_id)
    AND (@p_name       IS NULL OR name       LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active  IS NULL OR is_active  = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_session
  WHERE (@p_id         IS NULL OR id         = @p_id)
    AND (@p_name       IS NULL OR name       LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active  IS NULL OR is_active  = @p_is_active)
END
GO

-- chat history --

CREATE PROCEDURE dbo.upsert_chatbot_history(
  @p_id              NVARCHAR(36),
  @p_conversation_id NVARCHAR(36),
  @p_prompt          NVARCHAR(MAX),
  @p_response        NVARCHAR(MAX),
  @p_error           NVARCHAR(MAX),
  @p_is_active       BIT,
  @p_actor           NVARCHAR(255),
  @p_rag_collections NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_history_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_history
      (id, conversation_id, prompt, response, error,
       is_active, rag_collections, created_by, created_on)
    VALUES
      (@new_history_id, @p_conversation_id, @p_prompt, @p_response,
       @p_error, COALESCE(@p_is_active,1), @p_rag_collections, @p_actor, SYSUTCDATETIME())
    SELECT @new_history_id AS id
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_history
    SET conversation_id = COALESCE(@p_conversation_id, conversation_id),
        prompt          = COALESCE(@p_prompt, prompt),
        response        = COALESCE(@p_response, response),
        error           = COALESCE(@p_error, error),
        is_active       = COALESCE(@p_is_active, is_active),
        updated_by      = @p_actor,
        updated_on      = SYSUTCDATETIME(),
        rag_collections = COALESCE(@p_rag_collections, rag_collections)
    WHERE id = @p_id
    SELECT @p_id AS id
  END
END
GO

CREATE PROCEDURE dbo.filter_chatbot_history(
  @p_id              NVARCHAR(36),
  @p_conversation_id NVARCHAR(36),
  @p_prompt          NVARCHAR(255),
  @p_response        NVARCHAR(255),
  @p_created_by      NVARCHAR(255),
  @p_is_active       BIT,
  @p_page_number     INT,
  @p_page_size       INT
)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 10)
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit

  SELECT id, conversation_id, prompt, response, error, is_active,
         rag_collections, grounding_verdict, grounding_detail,
         created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_history
  WHERE (@p_id              IS NULL OR id              = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt          IS NULL OR prompt          LIKE '%' + @p_prompt   + '%')
    AND (@p_response        IS NULL OR response        LIKE '%' + @p_response + '%')
    AND (@p_created_by      IS NULL OR created_by      = @p_created_by)
    AND (@p_is_active       IS NULL OR is_active       = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_history
  WHERE (@p_id              IS NULL OR id              = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt          IS NULL OR prompt          LIKE '%' + @p_prompt   + '%')
    AND (@p_response        IS NULL OR response        LIKE '%' + @p_response + '%')
    AND (@p_created_by      IS NULL OR created_by      = @p_created_by)
    AND (@p_is_active       IS NULL OR is_active       = @p_is_active)
END
GO

-- chat feedback --

CREATE PROCEDURE dbo.upsert_chatbot_feedback(
  @p_id         NVARCHAR(36),
  @p_history_id NVARCHAR(36),
  @p_rating     NVARCHAR(20),
  @p_comment    NVARCHAR(MAX),
  @p_actor      NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_feedback_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_feedback (id, history_id, rating, comment, created_by, created_on)
    VALUES (@new_feedback_id, @p_history_id, @p_rating, @p_comment, @p_actor, SYSUTCDATETIME())
    SELECT @new_feedback_id AS id
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_feedback
    SET rating     = @p_rating,
        comment    = @p_comment,
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id
    SELECT @p_id AS id
  END
END
GO

CREATE PROCEDURE dbo.filter_chatbot_feedback(@p_history_id NVARCHAR(36))
AS
BEGIN
  SET NOCOUNT ON
  SELECT id, history_id, rating, comment, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_feedback
  WHERE (@p_history_id IS NULL OR history_id = @p_history_id)
  ORDER BY created_on DESC
END
GO

-- audit log / trace --

CREATE PROCEDURE dbo.upsert_chatbot_trace(
  @p_history_id     NVARCHAR(36),
  @p_chat_outcome   NVARCHAR(40),
  @p_model          NVARCHAR(100),
  @p_config_version NVARCHAR(40),
  @p_total_ms       INT,
  @p_top_retrieval_score FLOAT,
  @p_trace          NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON
  IF EXISTS (SELECT 1 FROM dbo.audit_log WHERE action='chat_trace' AND history_id=@p_history_id)
    UPDATE dbo.audit_log
    SET chat_outcome=@p_chat_outcome, model=@p_model, config_version=@p_config_version,
        total_ms=@p_total_ms, top_retrieval_score=@p_top_retrieval_score, detail=@p_trace
    WHERE action='chat_trace' AND history_id=@p_history_id
  ELSE
    INSERT INTO dbo.audit_log
      (id, actor, action, target, result, detail, history_id, chat_outcome, model, config_version, total_ms, top_retrieval_score)
    VALUES
      (NEWID(), 'system', 'chat_trace', @p_history_id, 'ok', @p_trace,
       @p_history_id, @p_chat_outcome, @p_model, @p_config_version, @p_total_ms, @p_top_retrieval_score)
END
GO

CREATE PROCEDURE dbo.get_chatbot_trace(@p_history_id NVARCHAR(36))
AS
BEGIN
  SET NOCOUNT ON
  SELECT
    h.id AS history_id, h.conversation_id, h.prompt, h.response, h.error,
    h.created_by, h.created_on, h.grounding_verdict, h.grounding_detail,
    t.chat_outcome, t.model, t.config_version, t.total_ms, t.top_retrieval_score, t.detail AS trace,
    f.rating AS feedback_rating, f.comment AS feedback_comment
  FROM dbo.chatbot_history h
  LEFT JOIN dbo.audit_log        t ON t.history_id = h.id AND t.action = 'chat_trace'
  LEFT JOIN dbo.chatbot_feedback f ON f.history_id = h.id
  WHERE h.id = @p_history_id
END
GO

CREATE PROCEDURE dbo.chat_metrics(@p_days INT)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @since DATETIME2 = DATEADD(DAY, -@p_days, SYSUTCDATETIME())
  SELECT
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since) AS turns,
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since AND error IS NOT NULL) AS errors,
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND chat_outcome='answered') AS answered,
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND chat_outcome='no_evidence') AS no_evidence,
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND chat_outcome IN ('chitchat','kb_overview')) AS small_talk,
    (SELECT AVG(CAST(total_ms AS FLOAT)) FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace') AS avg_total_ms,
    (SELECT TOP 1 PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_ms) OVER ()
       FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND total_ms IS NOT NULL) AS p95_total_ms,
    (SELECT COUNT(*)  FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating='positive') AS feedback_positive,
    (SELECT COUNT(*)  FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating='negative') AS feedback_negative,
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict='unsupported') AS grounding_unsupported,
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict='partial') AS grounding_partial
END
GO
