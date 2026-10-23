-- Applies all schema changes described in UPDATES.md:
--   1. Drop dbo.users (deprecated - DISABLE_AUTH=True)
--   2. GUID primary keys on session, history, feedback, audit_log (files keeps INT)
--   3. Merge chatbot_trace into audit_log (action='chat_trace' rows)
--   4. Add entity_id/tenant_id/user_id/user_type/is_admin to chatbot_session
-- Safe to re-run.

-- ============================================================
-- 0. Drop stored procedures
-- ============================================================

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_feedback;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_feedback;
GO
DROP PROCEDURE IF EXISTS dbo.update_chatbot_history_grounding;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.delete_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_sessions;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.get_chatbot_trace;
GO
DROP PROCEDURE IF EXISTS dbo.chat_metrics;
GO
DROP PROCEDURE IF EXISTS dbo.purge_audit_and_trace;
GO
DROP PROCEDURE IF EXISTS dbo.insert_audit_log;
GO
DROP PROCEDURE IF EXISTS dbo.filter_audit_log;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_user;
GO
DROP PROCEDURE IF EXISTS dbo.filter_users;
GO
DROP PROCEDURE IF EXISTS dbo.update_user_profile;
GO

-- ============================================================
-- 1. Drop tables (leaf-first)
-- ============================================================

IF OBJECT_ID('dbo.chatbot_feedback', 'U') IS NOT NULL DROP TABLE dbo.chatbot_feedback;
GO
IF OBJECT_ID('dbo.chatbot_trace',    'U') IS NOT NULL DROP TABLE dbo.chatbot_trace;
GO
IF OBJECT_ID('dbo.chatbot_history',  'U') IS NOT NULL DROP TABLE dbo.chatbot_history;
GO
IF OBJECT_ID('dbo.chatbot_session',  'U') IS NOT NULL DROP TABLE dbo.chatbot_session;
GO
IF OBJECT_ID('dbo.audit_log',        'U') IS NOT NULL DROP TABLE dbo.audit_log;
GO
IF OBJECT_ID('dbo.users',            'U') IS NOT NULL DROP TABLE dbo.users;
GO

-- ============================================================
-- 2. Recreate tables
-- ============================================================

CREATE TABLE dbo.audit_log (
  id             NVARCHAR(36)  NOT NULL DEFAULT NEWID() PRIMARY KEY,
  created_on     DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
  actor          NVARCHAR(255) NOT NULL,
  action         NVARCHAR(50)  NOT NULL,
  target         NVARCHAR(500) NULL,
  result         NVARCHAR(20)  NOT NULL DEFAULT 'ok',
  detail         NVARCHAR(MAX) NULL,
  history_id     NVARCHAR(36)  NULL,
  outcome        NVARCHAR(40)  NULL,
  model          NVARCHAR(100) NULL,
  config_version NVARCHAR(40)  NULL,
  total_ms       INT           NULL,
  top_score      FLOAT         NULL,
  CONSTRAINT ck_audit_log_detail_json CHECK (detail IS NULL OR ISJSON(detail) = 1)
)
GO

CREATE INDEX ix_audit_log_created_on ON dbo.audit_log (created_on DESC)
GO
CREATE INDEX ix_audit_log_history_id ON dbo.audit_log (history_id)
GO

CREATE TABLE dbo.chatbot_session (
  id          NVARCHAR(36)  NOT NULL DEFAULT NEWID() PRIMARY KEY,
  name        NVARCHAR(255) NOT NULL,
  entity_id   NVARCHAR(36)  NOT NULL DEFAULT '11111111-1111-1111-1111-111111111111',
  tenant_id   NVARCHAR(36)  NOT NULL DEFAULT '22222222-2222-2222-2222-222222222222',
  user_id     NVARCHAR(36)  NOT NULL DEFAULT '33333333-3333-3333-3333-333333333333',
  user_type   NVARCHAR(50)  NOT NULL DEFAULT 'demo_user',
  is_admin    BIT           NOT NULL DEFAULT 0,
  is_active   BIT           NOT NULL DEFAULT 1,
  owner_id    INT           NULL,
  created_by  NVARCHAR(255) NOT NULL,
  created_on  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_by  NVARCHAR(255) NULL,
  updated_on  DATETIME2     NULL
)
GO

CREATE INDEX ix_chatbot_session_owner ON dbo.chatbot_session (owner_id, created_on DESC)
GO

CREATE TABLE dbo.chatbot_history (
  id                    NVARCHAR(36)  NOT NULL DEFAULT NEWID() PRIMARY KEY,
  conversation_id       NVARCHAR(36)  NOT NULL,
  prompt                NVARCHAR(MAX) NOT NULL,
  response              NVARCHAR(MAX) NULL,
  feedback              NVARCHAR(20)  NULL,
  error                 NVARCHAR(MAX) NULL,
  comment               NVARCHAR(MAX) NULL,
  is_active             BIT           NOT NULL DEFAULT 1,
  owner_id              INT           NULL,
  retrieved_collections NVARCHAR(MAX) NULL,
  grounding_verdict     NVARCHAR(20)  NULL,
  grounding_detail      NVARCHAR(MAX) NULL,
  grounding_checked_on  DATETIME2     NULL,
  created_by            NVARCHAR(255) NOT NULL,
  created_on            DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_by            NVARCHAR(255) NULL,
  updated_on            DATETIME2     NULL,
  CONSTRAINT ck_chatbot_history_feedback  CHECK (feedback         IS NULL OR feedback         IN ('positive','negative')),
  CONSTRAINT ck_chatbot_history_grounding CHECK (grounding_verdict IS NULL OR grounding_verdict IN ('grounded','partial','unsupported'))
)
GO

CREATE INDEX ix_chatbot_history_session ON dbo.chatbot_history (conversation_id, created_on)
GO
CREATE INDEX ix_chatbot_history_owner ON dbo.chatbot_history (owner_id, created_on DESC)
GO

CREATE TABLE dbo.chatbot_feedback (
  id          NVARCHAR(36)  NOT NULL DEFAULT NEWID() PRIMARY KEY,
  history_id  NVARCHAR(36)  NOT NULL,
  rating      NVARCHAR(20)  NOT NULL,
  comment     NVARCHAR(MAX) NULL,
  owner_id    INT           NULL,
  created_by  NVARCHAR(255) NOT NULL,
  created_on  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_by  NVARCHAR(255) NULL,
  updated_on  DATETIME2     NULL,
  CONSTRAINT ck_chatbot_feedback_rating CHECK (rating IN ('positive','negative'))
)
GO

CREATE INDEX ix_chatbot_feedback_history ON dbo.chatbot_feedback (history_id)
GO

-- ============================================================
-- 3. Stored procedures
-- ============================================================

CREATE PROCEDURE dbo.insert_audit_log(
  @p_actor  NVARCHAR(255),
  @p_action NVARCHAR(50),
  @p_target NVARCHAR(500),
  @p_result NVARCHAR(20),
  @p_detail NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON
  INSERT INTO dbo.audit_log (id, actor, action, target, result, detail)
  VALUES (NEWID(), @p_actor, @p_action, @p_target, COALESCE(@p_result,'ok'), @p_detail)
END
GO

CREATE PROCEDURE dbo.filter_audit_log(
  @p_action      NVARCHAR(50),
  @p_actor       NVARCHAR(255),
  @p_page_number INT,
  @p_page_size   INT
)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 50)
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit

  SELECT id, created_on, actor, action, target, result, detail
  FROM dbo.audit_log
  WHERE action != 'chat_trace'
    AND (@p_action IS NULL OR action = @p_action)
    AND (@p_actor  IS NULL OR actor  = @p_actor)
  ORDER BY created_on DESC, id DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY

  SELECT COUNT(*) AS total_records
  FROM dbo.audit_log
  WHERE action != 'chat_trace'
    AND (@p_action IS NULL OR action = @p_action)
    AND (@p_actor  IS NULL OR actor  = @p_actor)
END
GO

CREATE PROCEDURE dbo.upsert_chatbot_trace(
  @p_history_id     NVARCHAR(36),
  @p_outcome        NVARCHAR(40),
  @p_model          NVARCHAR(100),
  @p_config_version NVARCHAR(40),
  @p_total_ms       INT,
  @p_top_score      FLOAT,
  @p_trace          NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON
  IF EXISTS (SELECT 1 FROM dbo.audit_log WHERE action='chat_trace' AND history_id=@p_history_id)
    UPDATE dbo.audit_log
    SET outcome=@p_outcome, model=@p_model, config_version=@p_config_version,
        total_ms=@p_total_ms, top_score=@p_top_score, detail=@p_trace
    WHERE action='chat_trace' AND history_id=@p_history_id
  ELSE
    INSERT INTO dbo.audit_log
      (id, actor, action, target, result, detail, history_id, outcome, model, config_version, total_ms, top_score)
    VALUES
      (NEWID(), 'system', 'chat_trace', @p_history_id, 'ok', @p_trace,
       @p_history_id, @p_outcome, @p_model, @p_config_version, @p_total_ms, @p_top_score)
END
GO

CREATE PROCEDURE dbo.get_chatbot_trace(@p_history_id NVARCHAR(36))
AS
BEGIN
  SET NOCOUNT ON
  SELECT
    h.id AS history_id, h.conversation_id, h.prompt, h.response, h.error,
    h.created_by, h.created_on, h.grounding_verdict, h.grounding_detail,
    t.outcome, t.model, t.config_version, t.total_ms, t.top_score, t.detail AS trace,
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
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND outcome='answered') AS answered,
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND outcome='no_evidence') AS no_evidence,
    (SELECT COUNT(*)  FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND outcome IN ('chitchat','kb_overview')) AS small_talk,
    (SELECT AVG(CAST(total_ms AS FLOAT)) FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace') AS avg_total_ms,
    (SELECT TOP 1 PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_ms) OVER ()
       FROM dbo.audit_log WHERE created_on >= @since AND action='chat_trace' AND total_ms IS NOT NULL) AS p95_total_ms,
    (SELECT COUNT(*)  FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating='positive') AS feedback_positive,
    (SELECT COUNT(*)  FROM dbo.chatbot_feedback WHERE created_on >= @since AND rating='negative') AS feedback_negative,
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict='unsupported') AS grounding_unsupported,
    (SELECT COUNT(*)  FROM dbo.chatbot_history WHERE created_on >= @since AND grounding_verdict='partial') AS grounding_partial
END
GO

CREATE PROCEDURE dbo.purge_audit_and_trace(@p_audit_days INT, @p_trace_days INT)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @audit INT = 0, @trace INT = 0
  IF @p_audit_days IS NOT NULL AND @p_audit_days > 0
  BEGIN
    DELETE FROM dbo.audit_log
    WHERE action != 'chat_trace' AND created_on < DATEADD(DAY, -@p_audit_days, SYSUTCDATETIME())
    SET @audit = @@ROWCOUNT
  END
  IF @p_trace_days IS NOT NULL AND @p_trace_days > 0
  BEGIN
    DELETE FROM dbo.audit_log
    WHERE action = 'chat_trace' AND created_on < DATEADD(DAY, -@p_trace_days, SYSUTCDATETIME())
    SET @trace = @@ROWCOUNT
  END
  SELECT @audit AS audit_rows_removed, @trace AS trace_rows_removed
END
GO

CREATE PROCEDURE dbo.upsert_chatbot_session(
  @p_id        NVARCHAR(36),
  @p_name      NVARCHAR(255),
  @p_is_active BIT,
  @p_actor     NVARCHAR(255),
  @p_owner_id  INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_session_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_session (id, name, is_active, owner_id, created_by, created_on)
    VALUES (@new_session_id, @p_name, COALESCE(@p_is_active,1), @p_owner_id, @p_actor, SYSUTCDATETIME())
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
  @p_page_size   INT,
  @p_owner_id    INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 10)
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit

  SELECT id, name, entity_id, tenant_id, user_id, user_type, is_admin,
         is_active, owner_id, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_session
  WHERE (@p_id         IS NULL OR id         = @p_id)
    AND (@p_name       IS NULL OR name       LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active  IS NULL OR is_active  = @p_is_active)
    AND (@p_owner_id   IS NULL OR owner_id   IS NULL OR owner_id = @p_owner_id)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_session
  WHERE (@p_id         IS NULL OR id         = @p_id)
    AND (@p_name       IS NULL OR name       LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active  IS NULL OR is_active  = @p_is_active)
    AND (@p_owner_id   IS NULL OR owner_id   IS NULL OR owner_id = @p_owner_id)
END
GO

CREATE PROCEDURE dbo.delete_chatbot_session(
  @p_id    NVARCHAR(36),
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON
  UPDATE dbo.chatbot_session
  SET is_active  = 0,
      updated_by = @p_actor,
      updated_on = SYSUTCDATETIME()
  WHERE id = @p_id
END
GO

CREATE PROCEDURE dbo.upsert_chatbot_history(
  @p_id                    NVARCHAR(36),
  @p_conversation_id       NVARCHAR(36),
  @p_prompt                NVARCHAR(MAX),
  @p_response              NVARCHAR(MAX),
  @p_feedback              NVARCHAR(20),
  @p_error                 NVARCHAR(MAX),
  @p_comment               NVARCHAR(MAX),
  @p_is_active             BIT,
  @p_actor                 NVARCHAR(255),
  @p_retrieved_collections NVARCHAR(MAX),
  @p_owner_id              INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_history_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_history
      (id, conversation_id, prompt, response, feedback, error, comment,
       is_active, owner_id, retrieved_collections, created_by, created_on)
    VALUES
      (@new_history_id, @p_conversation_id, @p_prompt, @p_response, @p_feedback,
       @p_error, @p_comment, COALESCE(@p_is_active,1), @p_owner_id,
       @p_retrieved_collections, @p_actor, SYSUTCDATETIME())
    SELECT @new_history_id AS id
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_history
    SET conversation_id       = COALESCE(@p_conversation_id, conversation_id),
        prompt                = COALESCE(@p_prompt, prompt),
        response              = COALESCE(@p_response, response),
        feedback              = COALESCE(@p_feedback, feedback),
        error                 = COALESCE(@p_error, error),
        comment               = COALESCE(@p_comment, comment),
        is_active             = COALESCE(@p_is_active, is_active),
        updated_by            = @p_actor,
        updated_on            = SYSUTCDATETIME(),
        retrieved_collections = COALESCE(@p_retrieved_collections, retrieved_collections)
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
  @p_feedback        NVARCHAR(20),
  @p_created_by      NVARCHAR(255),
  @p_is_active       BIT,
  @p_page_number     INT,
  @p_page_size       INT,
  @p_owner_id        INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  DECLARE @v_limit  INT = COALESCE(@p_page_size, 10)
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit

  SELECT id, conversation_id, prompt, response, feedback, error, comment, is_active,
         retrieved_collections, grounding_verdict, grounding_detail,
         created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_history
  WHERE (@p_id              IS NULL OR id              = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt          IS NULL OR prompt          LIKE '%' + @p_prompt   + '%')
    AND (@p_response        IS NULL OR response        LIKE '%' + @p_response + '%')
    AND (@p_feedback        IS NULL OR feedback        = @p_feedback)
    AND (@p_created_by      IS NULL OR created_by      = @p_created_by)
    AND (@p_is_active       IS NULL OR is_active       = @p_is_active)
    AND (@p_owner_id        IS NULL OR owner_id        IS NULL OR owner_id = @p_owner_id)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_history
  WHERE (@p_id              IS NULL OR id              = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt          IS NULL OR prompt          LIKE '%' + @p_prompt   + '%')
    AND (@p_response        IS NULL OR response        LIKE '%' + @p_response + '%')
    AND (@p_feedback        IS NULL OR feedback        = @p_feedback)
    AND (@p_created_by      IS NULL OR created_by      = @p_created_by)
    AND (@p_is_active       IS NULL OR is_active       = @p_is_active)
    AND (@p_owner_id        IS NULL OR owner_id        IS NULL OR owner_id = @p_owner_id)
END
GO

CREATE PROCEDURE dbo.update_chatbot_history_grounding(
  @p_id      NVARCHAR(36),
  @p_verdict NVARCHAR(20),
  @p_detail  NVARCHAR(MAX),
  @p_actor   NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON
  UPDATE dbo.chatbot_history
  SET grounding_verdict    = @p_verdict,
      grounding_detail     = @p_detail,
      grounding_checked_on = SYSUTCDATETIME(),
      updated_by           = @p_actor,
      updated_on           = SYSUTCDATETIME()
  WHERE id = @p_id
END
GO

CREATE PROCEDURE dbo.upsert_chatbot_feedback(
  @p_id         NVARCHAR(36),
  @p_history_id NVARCHAR(36),
  @p_rating     NVARCHAR(20),
  @p_comment    NVARCHAR(MAX),
  @p_actor      NVARCHAR(255),
  @p_owner_id   INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  IF @p_id IS NULL
  BEGIN
    DECLARE @new_feedback_id NVARCHAR(36) = NEWID()
    INSERT INTO dbo.chatbot_feedback (id, history_id, rating, comment, owner_id, created_by, created_on)
    VALUES (@new_feedback_id, @p_history_id, @p_rating, @p_comment, @p_owner_id, @p_actor, SYSUTCDATETIME())
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

CREATE PROCEDURE dbo.filter_chatbot_feedback(
  @p_history_id NVARCHAR(36),
  @p_owner_id   INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON
  SELECT id, history_id, rating, comment, owner_id, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_feedback
  WHERE (@p_history_id IS NULL OR history_id = @p_history_id)
    AND (@p_owner_id   IS NULL OR owner_id   IS NULL OR owner_id = @p_owner_id)
  ORDER BY created_on DESC
END
GO
