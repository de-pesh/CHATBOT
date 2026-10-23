-- Makes the grounding verdict (07_chatbot_grounding.sql) visible: adds grounding_verdict and
-- grounding_detail to what filter_chatbot_history returns, so GET /chat/history - and the chat
-- window's own history list - can show a caller their own answers' verdicts without a second
-- endpoint. grounding_checked_on is left out: nothing reads it yet.
--
-- Forward-only, like every file here: 04_chat_user_links.sql's filter_chatbot_history is not
-- edited in place (it already ran against provisioned databases), so this is the same procedure,
-- redefined, with the two columns added to its row SELECT only - its parameters and the
-- total_records SELECT are unchanged.

DROP PROCEDURE IF EXISTS dbo.filter_chatbot_history;
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
         retrieved_collections, grounding_verdict, grounding_detail
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
