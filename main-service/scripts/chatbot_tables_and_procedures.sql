-- Schema + stored procedures backing chatbot-inferencing-service
-- (app/routers/chatbot.py, app/services/mysql_service.py).
-- POST /chat was failing with 500 "An error occurred while executing a
-- database operation." because MySQL raised
-- "PROCEDURE ccpm.upsert_chatbot_session does not exist" -- the
-- chatbot_session / chatbot_history tables and their procedures were
-- never created in the DB.

-- =====================================================================
-- chatbot_session
-- =====================================================================
CREATE TABLE IF NOT EXISTS chatbot_session (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT 1,
  created_by VARCHAR(255) NOT NULL,
  created_on DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by VARCHAR(255) DEFAULT NULL,
  updated_on DATETIME DEFAULT NULL
);

-- =====================================================================
-- chatbot_history
-- =====================================================================
CREATE TABLE IF NOT EXISTS chatbot_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  conversation_id INT NOT NULL,
  prompt TEXT NOT NULL,
  response TEXT DEFAULT NULL,
  feedback VARCHAR(20) DEFAULT NULL,
  error TEXT DEFAULT NULL,
  comment TEXT DEFAULT NULL,
  is_active BOOLEAN NOT NULL DEFAULT 1,
  created_by VARCHAR(255) NOT NULL,
  created_on DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by VARCHAR(255) DEFAULT NULL,
  updated_on DATETIME DEFAULT NULL,
  intent VARCHAR(50) DEFAULT NULL,
  retrieved_collections TEXT DEFAULT NULL
);

DROP PROCEDURE IF EXISTS upsert_chatbot_session;
DROP PROCEDURE IF EXISTS filter_chatbot_sessions;
DROP PROCEDURE IF EXISTS delete_chatbot_session;
DROP PROCEDURE IF EXISTS upsert_chatbot_history;
DROP PROCEDURE IF EXISTS filter_chatbot_history;

DELIMITER $$

-- app/routers/chatbot.py: session_params = [id, name, is_active, actor]
CREATE PROCEDURE upsert_chatbot_session(
  IN p_id INT,
  IN p_name VARCHAR(255),
  IN p_is_active BOOLEAN,
  IN p_actor VARCHAR(255)
)
BEGIN
  IF p_id IS NULL THEN
    INSERT INTO chatbot_session (name, is_active, created_by, created_on)
    VALUES (p_name, COALESCE(p_is_active, 1), p_actor, NOW());
    SELECT LAST_INSERT_ID() AS id;
  ELSE
    UPDATE chatbot_session
    SET name = COALESCE(p_name, name),
        is_active = COALESCE(p_is_active, is_active),
        updated_by = p_actor,
        updated_on = NOW()
    WHERE id = p_id;
    SELECT p_id AS id;
  END IF;
END$$

-- Returns two result sets: matching rows, then a single-row total_records
-- (get_chat_session() reads results[1][0]['total_records']).
CREATE PROCEDURE filter_chatbot_sessions(
  IN p_id INT,
  IN p_name VARCHAR(255),
  IN p_created_by VARCHAR(255),
  IN p_is_active BOOLEAN,
  IN p_page_number INT,
  IN p_page_size INT
)
BEGIN
  DECLARE v_offset INT DEFAULT 0;
  DECLARE v_limit INT DEFAULT 10;
  SET v_limit = COALESCE(p_page_size, 10);
  SET v_offset = (COALESCE(p_page_number, 1) - 1) * v_limit;

  SELECT id, name, is_active, created_by, created_on, updated_by, updated_on
  FROM chatbot_session
  WHERE (p_id IS NULL OR id = p_id)
    AND (p_name IS NULL OR name LIKE CONCAT('%', p_name, '%'))
    AND (p_created_by IS NULL OR created_by = p_created_by)
    AND (p_is_active IS NULL OR is_active = p_is_active)
  ORDER BY created_on DESC
  LIMIT v_limit OFFSET v_offset;

  SELECT COUNT(*) AS total_records
  FROM chatbot_session
  WHERE (p_id IS NULL OR id = p_id)
    AND (p_name IS NULL OR name LIKE CONCAT('%', p_name, '%'))
    AND (p_created_by IS NULL OR created_by = p_created_by)
    AND (p_is_active IS NULL OR is_active = p_is_active);
END$$

-- Soft delete: app/routers/chatbot.py params = [session_id, user_id]
CREATE PROCEDURE delete_chatbot_session(
  IN p_id INT,
  IN p_actor VARCHAR(255)
)
BEGIN
  UPDATE chatbot_session
  SET is_active = 0,
      updated_by = p_actor,
      updated_on = NOW()
  WHERE id = p_id;
END$$

-- app/routers/chatbot.py: insert_params / update_params / error_params =
-- [id, conversation_id, prompt, response, feedback, error, comment,
--  is_active, actor, intent, retrieved_collections]
-- On update, NULL fields are left unchanged (COALESCE) since the router
-- calls this procedure multiple times for the same row (initial prompt
-- log, then a partial update with only the response, or only the error,
-- filled in).
CREATE PROCEDURE upsert_chatbot_history(
  IN p_id INT,
  IN p_conversation_id INT,
  IN p_prompt TEXT,
  IN p_response TEXT,
  IN p_feedback VARCHAR(20),
  IN p_error TEXT,
  IN p_comment TEXT,
  IN p_is_active BOOLEAN,
  IN p_actor VARCHAR(255),
  IN p_intent VARCHAR(50),
  IN p_retrieved_collections TEXT
)
BEGIN
  IF p_id IS NULL THEN
    INSERT INTO chatbot_history (
      conversation_id, prompt, response, feedback, error, comment,
      is_active, created_by, created_on, intent, retrieved_collections
    )
    VALUES (
      p_conversation_id, p_prompt, p_response, p_feedback, p_error, p_comment,
      COALESCE(p_is_active, 1), p_actor, NOW(), p_intent, p_retrieved_collections
    );
    SELECT LAST_INSERT_ID() AS id;
  ELSE
    UPDATE chatbot_history
    SET conversation_id = COALESCE(p_conversation_id, conversation_id),
        prompt = COALESCE(p_prompt, prompt),
        response = COALESCE(p_response, response),
        feedback = COALESCE(p_feedback, feedback),
        error = COALESCE(p_error, error),
        comment = COALESCE(p_comment, comment),
        is_active = COALESCE(p_is_active, is_active),
        updated_by = p_actor,
        updated_on = NOW(),
        intent = COALESCE(p_intent, intent),
        retrieved_collections = COALESCE(p_retrieved_collections, retrieved_collections)
    WHERE id = p_id;
    SELECT p_id AS id;
  END IF;
END$$

-- Returns two result sets: matching rows, then a single-row total_records
-- (get_chat_history() reads results[1][0]['total_records']).
CREATE PROCEDURE filter_chatbot_history(
  IN p_id INT,
  IN p_conversation_id INT,
  IN p_prompt VARCHAR(255),
  IN p_response VARCHAR(255),
  IN p_feedback VARCHAR(20),
  IN p_created_by VARCHAR(255),
  IN p_is_active BOOLEAN,
  IN p_page_number INT,
  IN p_page_size INT
)
BEGIN
  DECLARE v_offset INT DEFAULT 0;
  DECLARE v_limit INT DEFAULT 10;
  SET v_limit = COALESCE(p_page_size, 10);
  SET v_offset = (COALESCE(p_page_number, 1) - 1) * v_limit;

  SELECT id, conversation_id, prompt, response, feedback, error, comment,
         is_active, created_by, created_on, updated_by, updated_on,
         intent, retrieved_collections
  FROM chatbot_history
  WHERE (p_id IS NULL OR id = p_id)
    AND (p_conversation_id IS NULL OR conversation_id = p_conversation_id)
    AND (p_prompt IS NULL OR prompt LIKE CONCAT('%', p_prompt, '%'))
    AND (p_response IS NULL OR response LIKE CONCAT('%', p_response, '%'))
    AND (p_feedback IS NULL OR feedback = p_feedback)
    AND (p_created_by IS NULL OR created_by = p_created_by)
    AND (p_is_active IS NULL OR is_active = p_is_active)
  ORDER BY created_on DESC
  LIMIT v_limit OFFSET v_offset;

  SELECT COUNT(*) AS total_records
  FROM chatbot_history
  WHERE (p_id IS NULL OR id = p_id)
    AND (p_conversation_id IS NULL OR conversation_id = p_conversation_id)
    AND (p_prompt IS NULL OR prompt LIKE CONCAT('%', p_prompt, '%'))
    AND (p_response IS NULL OR response LIKE CONCAT('%', p_response, '%'))
    AND (p_feedback IS NULL OR feedback = p_feedback)
    AND (p_created_by IS NULL OR created_by = p_created_by)
    AND (p_is_active IS NULL OR is_active = p_is_active);
END$$

DELIMITER ;
