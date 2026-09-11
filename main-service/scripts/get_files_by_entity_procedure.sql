-- GET /files was failing with "PROCEDURE ccpm.get_files_by_entity does not exist".
--
-- It used to branch on an entity_type ('framework' | 'evidence') and join
-- through version_files -> versions, or through assessment_files. Those tables
-- went with the assessment and framework domains, and nothing supplies an
-- entity type any more, so the procedure now just returns the active files.
-- The two IN parameters are kept so the existing CALL signature still matches.

DROP PROCEDURE IF EXISTS get_files_by_entity;

DELIMITER $$

CREATE PROCEDURE get_files_by_entity(
  IN p_entity_type VARCHAR(50),
  IN p_entity_id INT
)
BEGIN
  SELECT id, name, mimetype, file_path, parsed_path, file_type,
         kb_file_id, is_active, created_by, created_on, updated_by, updated_on
  FROM files
  WHERE is_active = 1;
END$$

DELIMITER ;
