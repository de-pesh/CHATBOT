-- PATCH /files/{file_id}/status (app/routers/file.py: update_file_status).
-- The files table only has is_active BIT, so ACTIVE maps to 1 and both DISABLE
-- and INACTIVE map to 0. The FileLifecycleStatus enum value is not persisted
-- beyond what the caller already knows; the endpoint just toggles is_active.

DROP PROCEDURE IF EXISTS dbo.update_file_status;
GO

-- params = [file_id, is_active, actor]
CREATE PROCEDURE dbo.update_file_status(
  @p_id INT,
  @p_is_active BIT,
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.files
  SET is_active   = @p_is_active,
      updated_by  = @p_actor,
      updated_on  = SYSUTCDATETIME()
  WHERE id = @p_id;
END
GO
