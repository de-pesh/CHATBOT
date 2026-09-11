-- Schema + stored procedures backing app/controllers/file_controller.py
-- (POST /files was failing with "PROCEDURE ccpm.upsert_file does not exist"
-- because the `files` table and its procedures were never created in the DB.)

CREATE TABLE IF NOT EXISTS files (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  mimetype VARCHAR(100) NOT NULL,
  file_path VARCHAR(1000) NOT NULL,
  parsed_path VARCHAR(1000) DEFAULT NULL,
  file_type VARCHAR(50) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT 1,
  created_by VARCHAR(255) NOT NULL,
  created_on DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by VARCHAR(255) DEFAULT NULL,
  updated_on DATETIME DEFAULT NULL
);

DROP PROCEDURE IF EXISTS upsert_file;
DROP PROCEDURE IF EXISTS filter_files;
DROP PROCEDURE IF EXISTS delete_file;

DELIMITER $$

CREATE PROCEDURE upsert_file(
  IN p_id INT,
  IN p_name VARCHAR(255),
  IN p_mimetype VARCHAR(100),
  IN p_file_path VARCHAR(1000),
  IN p_parsed_path VARCHAR(1000),
  IN p_file_type VARCHAR(50),
  IN p_is_active BOOLEAN,
  IN p_updated_by VARCHAR(255)
)
BEGIN
  IF p_id IS NULL THEN
    INSERT INTO files (name, mimetype, file_path, parsed_path, file_type, is_active, created_by, created_on)
    VALUES (p_name, p_mimetype, p_file_path, p_parsed_path, p_file_type, COALESCE(p_is_active, 1), p_updated_by, NOW());
    SELECT LAST_INSERT_ID() AS id;
  ELSE
    UPDATE files
    SET name = p_name,
        mimetype = p_mimetype,
        file_path = p_file_path,
        parsed_path = p_parsed_path,
        file_type = p_file_type,
        is_active = p_is_active,
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

  SELECT id, name, mimetype, file_path, parsed_path, file_type, is_active,
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

CREATE PROCEDURE delete_file(
  IN p_id INT,
  IN p_actor VARCHAR(255)
)
BEGIN
  UPDATE files
  SET is_active = 0,
      updated_by = p_actor,
      updated_on = NOW()
  WHERE id = p_id;
END$$

DELIMITER ;
