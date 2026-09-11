-- Adds knowledge-base linkage to the `files` table.
--
-- POST /files now accepts add_to_knowledge_base=true, which pushes the
-- uploaded document into chatbot-inferencing-service's /kb endpoint
-- (parsing + vector indexing) and stores the returned document id here,
-- so it can later be looked up / removed from the KB on file delete.

SET @kb_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'files' AND COLUMN_NAME = 'kb_file_id'
);
SET @kb_col_sql = IF(
  @kb_col_exists = 0,
  'ALTER TABLE files ADD COLUMN kb_file_id VARCHAR(255) DEFAULT NULL AFTER file_type',
  'SELECT 1'
);
PREPARE kb_col_stmt FROM @kb_col_sql;
EXECUTE kb_col_stmt;
DEALLOCATE PREPARE kb_col_stmt;

DROP PROCEDURE IF EXISTS upsert_file;
DROP PROCEDURE IF EXISTS filter_files;

DELIMITER $$

CREATE PROCEDURE upsert_file(
  IN p_id INT,
  IN p_name VARCHAR(255),
  IN p_mimetype VARCHAR(100),
  IN p_file_path VARCHAR(1000),
  IN p_parsed_path VARCHAR(1000),
  IN p_file_type VARCHAR(50),
  IN p_is_active BOOLEAN,
  IN p_kb_file_id VARCHAR(255),
  IN p_updated_by VARCHAR(255)
)
BEGIN
  IF p_id IS NULL THEN
    INSERT INTO files (name, mimetype, file_path, parsed_path, file_type, is_active, kb_file_id, created_by, created_on)
    VALUES (p_name, p_mimetype, p_file_path, p_parsed_path, p_file_type, COALESCE(p_is_active, 1), p_kb_file_id, p_updated_by, NOW());
    SELECT LAST_INSERT_ID() AS id;
  ELSE
    UPDATE files
    SET name = p_name,
        mimetype = p_mimetype,
        file_path = p_file_path,
        parsed_path = p_parsed_path,
        file_type = p_file_type,
        is_active = p_is_active,
        kb_file_id = p_kb_file_id,
        updated_by = p_updated_by,
        updated_on = NOW()
    WHERE id = p_id;
    SELECT p_id AS id;
  END IF;
END$$

CREATE PROCEDURE filter_files(
  IN p_id INT,
  IN p_name VARCHAR(255),
  IN p_mimetype VARCHAR(100),
  IN p_file_type VARCHAR(50),
  IN p_is_active BOOLEAN,
  IN p_page_number INT,
  IN p_page_size INT
)
BEGIN
  DECLARE v_offset INT DEFAULT 0;
  DECLARE v_limit INT DEFAULT 10;
  SET v_limit = COALESCE(p_page_size, 10);
  SET v_offset = (COALESCE(p_page_number, 1) - 1) * v_limit;

  SELECT id, name, mimetype, file_path, parsed_path, file_type, kb_file_id, is_active,
         created_by, created_on, updated_by, updated_on
  FROM files
  WHERE (p_id IS NULL OR id = p_id)
    AND (p_name IS NULL OR name LIKE CONCAT('%', p_name, '%'))
    AND (p_mimetype IS NULL OR mimetype = p_mimetype)
    AND (p_file_type IS NULL OR file_type = p_file_type)
    AND (p_is_active IS NULL OR is_active = p_is_active)
  ORDER BY created_on DESC
  LIMIT v_limit OFFSET v_offset;
END$$

DELIMITER ;
