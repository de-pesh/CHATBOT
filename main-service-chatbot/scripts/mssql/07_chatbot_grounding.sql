-- Whether an answer's citations are actually supported by what they cite, checked once after
-- the answer has already been sent to the browser (app/controllers/chat_grounding.py, called
-- from app/routers/chat.py as a fire-and-forget background task) so it can never slow down or
-- fail a chat turn - see Settings.GROUNDING_CHECK_ENABLED (off by default).
--
-- Columns added to chatbot_history itself, unlike chatbot_feedback (06_chatbot_feedback.sql):
-- a grounding verdict is intrinsic metadata about the turn - the same kind of thing
-- `response`/`retrieved_collections` already are - not a distinct user action that might one
-- day need a history of its own, so it does not need a table of its own.

IF COL_LENGTH('dbo.chatbot_history', 'grounding_verdict') IS NULL
  ALTER TABLE dbo.chatbot_history ADD grounding_verdict NVARCHAR(20) NULL
    CHECK (grounding_verdict IN ('grounded', 'partial', 'unsupported'));
GO

IF COL_LENGTH('dbo.chatbot_history', 'grounding_detail') IS NULL
  ALTER TABLE dbo.chatbot_history ADD grounding_detail NVARCHAR(MAX) NULL;
GO

IF COL_LENGTH('dbo.chatbot_history', 'grounding_checked_on') IS NULL
  ALTER TABLE dbo.chatbot_history ADD grounding_checked_on DATETIME2 NULL;
GO

DROP PROCEDURE IF EXISTS dbo.update_chatbot_history_grounding;
GO

-- app/logic/grounding.py: grounding_update_params = [id, verdict, detail, actor]
CREATE PROCEDURE dbo.update_chatbot_history_grounding(
  @p_id INT,
  @p_verdict NVARCHAR(20),
  @p_detail NVARCHAR(MAX),
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.chatbot_history
  SET grounding_verdict = @p_verdict,
      grounding_detail = @p_detail,
      grounding_checked_on = SYSUTCDATETIME(),
      updated_by = @p_actor,
      updated_on = SYSUTCDATETIME()
  WHERE id = @p_id;
END
GO
