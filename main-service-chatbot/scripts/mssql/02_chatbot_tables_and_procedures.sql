-- T-SQL port of ../chatbot_tables_and_procedures.sql and
-- ../remove_chat_intent_column.sql, folded into final-state schema (see the
-- note at the top of 01_files_table_and_procedures.sql - no MySQL migration
-- history to replay for a backend with no prior deployment).

IF OBJECT_ID('dbo.chatbot_session', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.chatbot_session (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(255) NOT NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_by NVARCHAR(255) NOT NULL,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by NVARCHAR(255) NULL,
    updated_on DATETIME2 NULL
  );
END
GO

IF OBJECT_ID('dbo.chatbot_history', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.chatbot_history (
    id INT IDENTITY(1,1) PRIMARY KEY,
    conversation_id INT NOT NULL,
    prompt NVARCHAR(MAX) NOT NULL,
    response NVARCHAR(MAX) NULL,
    feedback NVARCHAR(20) NULL,
    error NVARCHAR(MAX) NULL,
    comment NVARCHAR(MAX) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_by NVARCHAR(255) NOT NULL,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by NVARCHAR(255) NULL,
    updated_on DATETIME2 NULL,
    retrieved_collections NVARCHAR(MAX) NULL
  );
END
GO

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_sessions;
GO
DROP PROCEDURE IF EXISTS dbo.delete_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_history;
GO

-- app/routers/chat.py: session_params = [id, name, is_active, actor]
CREATE PROCEDURE dbo.upsert_chatbot_session(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_is_active BIT,
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.chatbot_session (name, is_active, created_by, created_on)
    VALUES (@p_name, COALESCE(@p_is_active, 1), @p_actor, SYSUTCDATETIME());
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_session
    SET name = COALESCE(@p_name, name),
        is_active = COALESCE(@p_is_active, is_active),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

-- Returns two result sets: matching rows, then a single-row total_records
-- (get_chat_session() reads results[1][0]['total_records']).
CREATE PROCEDURE dbo.filter_chatbot_sessions(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_created_by NVARCHAR(255),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit INT = COALESCE(@p_page_size, 10);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT id, name, is_active, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_session
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_name IS NULL OR name LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_session
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_name IS NULL OR name LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active IS NULL OR is_active = @p_is_active);
END
GO

-- Soft delete: app/routers/chat.py params = [session_id, user_id]
CREATE PROCEDURE dbo.delete_chatbot_session(
  @p_id INT,
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.chatbot_session
  SET is_active = 0,
      updated_by = @p_actor,
      updated_on = SYSUTCDATETIME()
  WHERE id = @p_id;
END
GO

-- app/routers/chat.py: insert_params / update_params / error_params =
-- [id, conversation_id, prompt, response, feedback, error, comment,
--  is_active, actor, retrieved_collections]
-- On update, NULL fields are left unchanged (COALESCE) since the router
-- calls this procedure multiple times for the same row (initial prompt
-- log, then a partial update with only the response, or only the error,
-- filled in).
CREATE PROCEDURE dbo.upsert_chatbot_history(
  @p_id INT,
  @p_conversation_id INT,
  @p_prompt NVARCHAR(MAX),
  @p_response NVARCHAR(MAX),
  @p_feedback NVARCHAR(20),
  @p_error NVARCHAR(MAX),
  @p_comment NVARCHAR(MAX),
  @p_is_active BIT,
  @p_actor NVARCHAR(255),
  @p_retrieved_collections NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.chatbot_history (
      conversation_id, prompt, response, feedback, error, comment,
      is_active, created_by, created_on, retrieved_collections
    )
    VALUES (
      @p_conversation_id, @p_prompt, @p_response, @p_feedback, @p_error, @p_comment,
      COALESCE(@p_is_active, 1), @p_actor, SYSUTCDATETIME(), @p_retrieved_collections
    );
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_history
    SET conversation_id = COALESCE(@p_conversation_id, conversation_id),
        prompt = COALESCE(@p_prompt, prompt),
        response = COALESCE(@p_response, response),
        feedback = COALESCE(@p_feedback, feedback),
        error = COALESCE(@p_error, error),
        comment = COALESCE(@p_comment, comment),
        is_active = COALESCE(@p_is_active, is_active),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME(),
        retrieved_collections = COALESCE(@p_retrieved_collections, retrieved_collections)
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

-- Returns two result sets: matching rows, then a single-row total_records
-- (get_chat_history() reads results[1][0]['total_records']).
CREATE PROCEDURE dbo.filter_chatbot_history(
  @p_id INT,
  @p_conversation_id INT,
  @p_prompt NVARCHAR(255),
  @p_response NVARCHAR(255),
  @p_feedback NVARCHAR(20),
  @p_created_by NVARCHAR(255),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit INT = COALESCE(@p_page_size, 10);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT id, conversation_id, prompt, response, feedback, error, comment,
         is_active, created_by, created_on, updated_by, updated_on,
         retrieved_collections
  FROM dbo.chatbot_history
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt IS NULL OR prompt LIKE '%' + @p_prompt + '%')
    AND (@p_response IS NULL OR response LIKE '%' + @p_response + '%')
    AND (@p_feedback IS NULL OR feedback = @p_feedback)
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_history
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_conversation_id IS NULL OR conversation_id = @p_conversation_id)
    AND (@p_prompt IS NULL OR prompt LIKE '%' + @p_prompt + '%')
    AND (@p_response IS NULL OR response LIKE '%' + @p_response + '%')
    AND (@p_feedback IS NULL OR feedback = @p_feedback)
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active IS NULL OR is_active = @p_is_active);
END
GO
