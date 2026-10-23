-- Thumbs up/down on an answer, with an optional comment (mainly for a thumbs
-- down). A separate table rather than the unused dbo.chatbot_history.feedback/
-- comment columns left over from before this app had per-turn history rows
-- worth rating: nothing writes those columns today, and what this data is used
-- for (surfacing it to admins, feeding it back into ranking, ...) is still
-- undecided, so keeping it apart from chatbot_history costs nothing and keeps
-- that decision open.
--
-- history_id has no FOREIGN KEY to dbo.chatbot_history and owner_id none to
-- dbo.users - same precedent as owner_id on chatbot_session/chatbot_history
-- (scripts/mssql/04_chat_user_links.sql): ownership is enforced in
-- app/routers/chat.py (a caller can only rate a chat turn that is their own),
-- not by the database rejecting a bad id.

IF OBJECT_ID('dbo.chatbot_feedback', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.chatbot_feedback (
    id INT IDENTITY(1,1) PRIMARY KEY,
    history_id INT NOT NULL,
    rating NVARCHAR(20) NOT NULL CHECK (rating IN ('positive', 'negative')),
    comment NVARCHAR(MAX) NULL,
    owner_id INT NULL,
    created_by NVARCHAR(255) NOT NULL,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by NVARCHAR(255) NULL,
    updated_on DATETIME2 NULL
  );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_feedback_history' AND object_id = OBJECT_ID('dbo.chatbot_feedback'))
  CREATE INDEX ix_chatbot_feedback_history ON dbo.chatbot_feedback (history_id);
GO

DROP PROCEDURE IF EXISTS dbo.upsert_chatbot_feedback;
GO
DROP PROCEDURE IF EXISTS dbo.filter_chatbot_feedback;
GO

-- app/logic/feedback.py: feedback_params = [id, history_id, rating, comment, actor, owner_id]
-- Unlike upsert_chatbot_history, an update always replaces rating and comment outright (no
-- COALESCE): the caller always submits the full, current state of their feedback, not a partial
-- patch, so switching from a commented thumbs-down to a plain thumbs-up must clear the comment.
CREATE PROCEDURE dbo.upsert_chatbot_feedback(
  @p_id INT,
  @p_history_id INT,
  @p_rating NVARCHAR(20),
  @p_comment NVARCHAR(MAX),
  @p_actor NVARCHAR(255),
  @p_owner_id INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.chatbot_feedback (history_id, rating, comment, owner_id, created_by, created_on)
    VALUES (@p_history_id, @p_rating, @p_comment, @p_owner_id, @p_actor, SYSUTCDATETIME());
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.chatbot_feedback
    SET rating = @p_rating,
        comment = @p_comment,
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

-- One result set, no paging: an internal lookup (is there already feedback on this chat turn?
-- see app/routers/chat.py: submit_chat_feedback) rather than a public listing endpoint - there
-- isn't one yet, since what this data is used for is still undecided (see the note above).
-- @p_owner_id follows the same rule as every other filter_* procedure here: a row with no owner
-- stays visible (the DISABLE_AUTH dev identity's rows all share that), one with a different
-- owner does not.
CREATE PROCEDURE dbo.filter_chatbot_feedback(
  @p_history_id INT,
  @p_owner_id INT = NULL
)
AS
BEGIN
  SET NOCOUNT ON;
  SELECT id, history_id, rating, comment, owner_id, created_by, created_on, updated_by, updated_on
  FROM dbo.chatbot_feedback
  WHERE (@p_history_id IS NULL OR history_id = @p_history_id)
    AND (@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id)
  ORDER BY created_on DESC;
END
GO
