-- Admin audit trail, a per-turn trace of what the chatbot did, and a content hash on files.
-- Forward-only and re-runnable, like the scripts before it.

-- --- Files: content hash, so the same document is not ingested twice by accident ---

IF COL_LENGTH('dbo.files', 'content_hash') IS NULL
  ALTER TABLE dbo.files ADD content_hash NVARCHAR(64) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_files_content_hash' AND object_id = OBJECT_ID('dbo.files'))
  CREATE INDEX ix_files_content_hash ON dbo.files (content_hash);
GO

DROP PROCEDURE IF EXISTS dbo.set_file_content_hash;
GO
DROP PROCEDURE IF EXISTS dbo.find_file_by_content_hash;
GO

-- [id, sha256 hex]
CREATE PROCEDURE dbo.set_file_content_hash(@p_id INT, @p_content_hash NVARCHAR(64))
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files SET content_hash = @p_content_hash WHERE id = @p_id;
END
GO

-- [sha256 hex]: the newest active file with this content, if any.
CREATE PROCEDURE dbo.find_file_by_content_hash(@p_content_hash NVARCHAR(64))
AS
BEGIN
  SET NOCOUNT ON;
  SELECT TOP 1 id, name, processing_status, created_on
  FROM dbo.files
  WHERE content_hash = @p_content_hash AND is_active = 1
  ORDER BY created_on DESC;
END
GO

-- --- Audit log: who did what to what, and how it went ---

IF OBJECT_ID('dbo.audit_log', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.audit_log (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    actor NVARCHAR(255) NOT NULL,
    action NVARCHAR(50) NOT NULL,
    target NVARCHAR(500) NULL,
    result NVARCHAR(20) NOT NULL DEFAULT 'ok',
    detail NVARCHAR(MAX) NULL
  );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_audit_log_created_on' AND object_id = OBJECT_ID('dbo.audit_log'))
  CREATE INDEX ix_audit_log_created_on ON dbo.audit_log (created_on DESC);
GO

DROP PROCEDURE IF EXISTS dbo.insert_audit_log;
GO
DROP PROCEDURE IF EXISTS dbo.filter_audit_log;
GO
DROP PROCEDURE IF EXISTS dbo.purge_audit_and_trace;
GO

-- [actor, action, target, result, detail]
CREATE PROCEDURE dbo.insert_audit_log(
  @p_actor NVARCHAR(255),
  @p_action NVARCHAR(50),
  @p_target NVARCHAR(500),
  @p_result NVARCHAR(20),
  @p_detail NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON;
  INSERT INTO dbo.audit_log (actor, action, target, result, detail)
  VALUES (@p_actor, @p_action, @p_target, COALESCE(@p_result, 'ok'), @p_detail);
END
GO

-- [action, actor, page_number, page_size]: newest first, two result sets (rows, then total_records).
CREATE PROCEDURE dbo.filter_audit_log(
  @p_action NVARCHAR(50),
  @p_actor NVARCHAR(255),
  @p_page_number INT,
  @p_page_size INT
)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, created_on, actor, action, target, result, detail
  FROM dbo.audit_log
  WHERE (@p_action IS NULL OR action = @p_action)
    AND (@p_actor IS NULL OR actor = @p_actor)
  ORDER BY created_on DESC, id DESC
  OFFSET (@p_page_number - 1) * @p_page_size ROWS FETCH NEXT @p_page_size ROWS ONLY;

  SELECT COUNT(*) AS total_records
  FROM dbo.audit_log
  WHERE (@p_action IS NULL OR action = @p_action)
    AND (@p_actor IS NULL OR actor = @p_actor);
END
GO

-- --- Chat trace: what one turn retrieved, decided and ran on ---

IF OBJECT_ID('dbo.chatbot_trace', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.chatbot_trace (
    history_id INT NOT NULL PRIMARY KEY,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    -- 'answered' | 'no_evidence' | 'chitchat' | 'kb_overview'
    outcome NVARCHAR(20) NOT NULL,
    model NVARCHAR(100) NULL,
    config_version NVARCHAR(20) NULL,
    total_ms INT NULL,
    top_score FLOAT NULL,
    trace NVARCHAR(MAX) NULL,
    CONSTRAINT ck_chatbot_trace_json CHECK (trace IS NULL OR ISJSON(trace) = 1)
  );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_trace_created_on' AND object_id = OBJECT_ID('dbo.chatbot_trace'))
  CREATE INDEX ix_chatbot_trace_created_on ON dbo.chatbot_trace (created_on DESC);
GO

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.get_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.chat_metrics;
GO

-- [history_id, outcome, model, config_version, total_ms, top_score, trace json]
CREATE PROCEDURE dbo.upsert_chatbot_trace(
  @p_history_id INT,
  @p_outcome NVARCHAR(20),
  @p_model NVARCHAR(100),
  @p_config_version NVARCHAR(20),
  @p_total_ms INT,
  @p_top_score FLOAT,
  @p_trace NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF EXISTS (SELECT 1 FROM dbo.chatbot_trace WHERE history_id = @p_history_id)
    UPDATE dbo.chatbot_trace
    SET outcome = @p_outcome, model = @p_model, config_version = @p_config_version,
        total_ms = @p_total_ms, top_score = @p_top_score, trace = @p_trace
    WHERE history_id = @p_history_id;
  ELSE
    INSERT INTO dbo.chatbot_trace (history_id, outcome, model, config_version, total_ms, top_score, trace)
    VALUES (@p_history_id, @p_outcome, @p_model, @p_config_version, @p_total_ms, @p_top_score, @p_trace);
END
GO

-- [history_id]: the turn, its trace and any feedback on it, for an admin working out why an answer went wrong.
CREATE PROCEDURE dbo.get_chatbot_trace(@p_history_id INT)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT h.id AS history_id, h.conversation_id, h.prompt, h.response, h.error, h.created_by, h.created_on,
         h.grounding_verdict, h.grounding_detail,
         t.outcome, t.model, t.config_version, t.total_ms, t.top_score, t.trace,
         f.rating AS feedback_rating, f.comment AS feedback_comment
  FROM dbo.chatbot_history h
  LEFT JOIN dbo.chatbot_trace t ON t.history_id = h.id
  LEFT JOIN dbo.chatbot_feedback f ON f.history_id = h.id
  WHERE h.id = @p_history_id;
END
GO

-- [days]: rates and latency over the last N days, one row.
CREATE PROCEDURE dbo.chat_metrics(@p_days INT)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @since DATETIME2 = DATEADD(DAY, -@p_days, SYSUTCDATETIME());

  SELECT
    (SELECT COUNT(*) FROM dbo.chatbot_history WHERE created_on >= @since) AS turns,
    (SELECT COUNT(*) FROM dbo.chatbot_history WHERE created_on >= @since AND error IS NOT NULL) AS errors,
    (SELECT COUNT(*) FROM dbo.chatbot_trace WHERE created_on >= @since AND outcome = 'answered') AS answered,
    (SELECT COUNT(*) FROM dbo.chatbot_trace WHERE created_on >= @since AND outcome = 'no_evidence') AS no_evidence,
    (SELECT COUNT(*) FROM dbo.chatbot_trace WHERE created_on >= @since AND outcome IN ('chitchat', 'kb_overview')) AS small_talk,
    (SELECT AVG(CAST(total_ms AS FLOAT)) FROM dbo.chatbot_trace WHERE created_on >= @since) AS avg_total_ms,
    (SELECT TOP 1 PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_ms) OVER ()
       FROM dbo.chatbot_trace WHERE created_on >= @since AND total_ms IS NOT NULL) AS p95_total_ms,
    (SELECT COUNT(*) FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating = 'positive') AS feedback_positive,
    (SELECT COUNT(*) FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating = 'negative') AS feedback_negative,
    (SELECT COUNT(*) FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict = 'unsupported') AS grounding_unsupported,
    (SELECT COUNT(*) FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict = 'partial') AS grounding_partial;
END
GO

-- [audit_days, trace_days]: retention. NULL or <= 0 keeps that table as it is. Returns rows removed.
CREATE PROCEDURE dbo.purge_audit_and_trace(@p_audit_days INT, @p_trace_days INT)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @audit INT = 0, @trace INT = 0;
  IF @p_audit_days IS NOT NULL AND @p_audit_days > 0
  BEGIN
    DELETE FROM dbo.audit_log WHERE created_on < DATEADD(DAY, -@p_audit_days, SYSUTCDATETIME());
    SET @audit = @@ROWCOUNT;
  END
  IF @p_trace_days IS NOT NULL AND @p_trace_days > 0
  BEGIN
    DELETE FROM dbo.chatbot_trace WHERE created_on < DATEADD(DAY, -@p_trace_days, SYSUTCDATETIME());
    SET @trace = @@ROWCOUNT;
  END
  SELECT @audit AS audit_rows_removed, @trace AS trace_rows_removed;
END
GO
