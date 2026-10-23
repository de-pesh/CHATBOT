-- Ties chat sessions and history to dbo.users (05_users.sql), so who owns a
-- conversation is a real account, not just an email string a caller cannot
-- be stopped from repeating. owner_id has no FOREIGN KEY - files.created_by
-- already sets the precedent of no FKs in this schema, and the alternative
-- would force this script to run after 05_users.sql for no real benefit,
-- since ownership is enforced in the stored procedures below, not by the
-- database rejecting a bad id.
--
-- owner_id is nullable on purpose: the DISABLE_AUTH dev identity (id 0, see
-- app/dependencies.get_current_user) has no row in dbo.users, so it always
-- passes NULL. Every caller in that mode shares the one created_by
-- ("dev@local.dev") anyway, which is what actually scopes those rows - a
-- row that HAS an owner_id is only ever visible to that owner.

IF COL_LENGTH('dbo.chatbot_session', 'owner_id') IS NULL
  ALTER TABLE dbo.chatbot_session ADD owner_id INT NULL;
GO

IF COL_LENGTH('dbo.chatbot_history', 'owner_id') IS NULL
  ALTER TABLE dbo.chatbot_history ADD owner_id INT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_session_owner' AND object_id = OBJECT_ID('dbo.chatbot_session'))
  CREATE INDEX ix_chatbot_session_owner ON dbo.chatbot_session (owner_id, created_on DESC);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_history_owner' AND object_id = OBJECT_ID('dbo.chatbot_history'))
  CREATE INDEX ix_chatbot_history_owner ON dbo.chatbot_history (owner_id, created_on DESC);
GO

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_session;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_sessions;
GO
DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_history;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_history;
GO

-- params = [id, name, is_active, actor, owner_id]. On update, a row that has no owner yet is
-- linked to the caller; one that has keeps its own.
CREATE PROCEDURE dbo.upsert_chatbot_session(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_is_active BIT,
  @p_actor NVARCHAR(255),
  @p_owner_id INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.chatbot_session (name, is_active, created_by, created_on, owner_id)
    VALUES (@p_name, COALESCE(@p_is_active, 1), @p_actor, SYSUTCDATETIME(), @p_owner_id);
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_session
    SET name = COALESCE(@p_name, name),
        is_active = COALESCE(@p_is_active, is_active),
        owner_id = COALESCE(owner_id, @p_owner_id),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

-- Two result sets: the rows, then a single-row total_records. @p_owner_id, when given, hides
-- rows that belong to a different owner; rows with none stay visible to whoever matches
-- @p_created_by.
CREATE PROCEDURE dbo.filter_chatbot_sessions(
  @p_id INT,
  @p_name NVARCHAR(255),
  @p_created_by NVARCHAR(255),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT,
  @p_owner_id INT = NULL
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
    AND (@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;

  SELECT COUNT(*) AS total_records
  FROM dbo.chatbot_session
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_name IS NULL OR name LIKE '%' + @p_name + '%')
    AND (@p_created_by IS NULL OR created_by = @p_created_by)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
    AND (@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id);
END
GO

-- params = [id, conversation_id, prompt, response, feedback, error, comment,
--           is_active, actor, retrieved_collections, owner_id]
-- NULL fields are left unchanged on update, as before; a row with no owner yet is linked to
-- the caller.
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
  @p_retrieved_collections NVARCHAR(MAX),
  @p_owner_id INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.chatbot_history (
      conversation_id, prompt, response, feedback, error, comment,
      is_active, created_by, created_on, retrieved_collections, owner_id
    )
    VALUES (
      @p_conversation_id, @p_prompt, @p_response, @p_feedback, @p_error, @p_comment,
      COALESCE(@p_is_active, 1), @p_actor, SYSUTCDATETIME(), @p_retrieved_collections, @p_owner_id
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
        owner_id = COALESCE(owner_id, @p_owner_id),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME(),
        retrieved_collections = COALESCE(@p_retrieved_collections, retrieved_collections)
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

CREATE PROCEDURE dbo.filter_chatbot_history(
  @p_id INT,
  @p_conversation_id INT,
  @p_prompt NVARCHAR(255),
  @p_response NVARCHAR(255),
  @p_feedback NVARCHAR(20),
  @p_created_by NVARCHAR(255),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT,
  @p_owner_id INT = NULL
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
    AND (@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id)
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
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
    AND (@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id);
END
GO
