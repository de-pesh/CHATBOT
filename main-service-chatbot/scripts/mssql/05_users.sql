-- The real login identity behind the chat: dbo.users. This is the gap
-- docs/guide.md and stored_procedures.py have flagged since the old RBAC
-- domain was cut down to just the chatbot's needs: filter_users and
-- upsert_user are called by app/controllers/auth_controller.py and
-- app/dependencies.get_current_user, but until now nothing created the table
-- they query, so every real login failed and DISABLE_AUTH=True was the only
-- way to run this service at all - every caller was folded into the one
-- dev@local.dev identity, and every chatbot_session / chatbot_history row
-- belonged to that same identity too.
--
-- Only two roles exist (app/models/auth.py: Role = "admin" | "user";
-- app/dependencies.require_admin), so role is a plain column with a CHECK,
-- not a roles/permissions/role_permissions join - three tables with nothing
-- to put in them beyond two rows and three permission strings. Likewise no
-- organizations table: nothing in this service is scoped by one.
--
-- display_name and preferences are what the chat prompts read
-- (app/logic/user_context.py) - user-editable via PATCH /chat/me. They used
-- to live on a separate dbo.chat_user (see 03_chat_user.sql); merged in here
-- once every caller had a real dbo.users row to put them on instead.
--
-- Once this is applied, create a first account with
-- scripts/create_superuser.py and set DISABLE_AUTH=False.

IF OBJECT_ID('dbo.users', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.users (
    id INT IDENTITY(1,1) PRIMARY KEY,
    first_name NVARCHAR(255) NOT NULL,
    last_name NVARCHAR(255) NOT NULL,
    email NVARCHAR(255) NOT NULL,
    hashed_password NVARCHAR(255) NOT NULL,
    -- 'admin': can upload/delete files and run ingestion (app/routers/file.py).
    -- 'user': can only chat.
    role NVARCHAR(20) NOT NULL DEFAULT 'user' CONSTRAINT ck_users_role CHECK (role IN ('admin', 'user')),
    is_active BIT NOT NULL DEFAULT 1,
    created_by NVARCHAR(255) NOT NULL,
    created_on DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_by NVARCHAR(255) NULL,
    updated_on DATETIME2 NULL,
    CONSTRAINT uq_users_email UNIQUE (email)
  );
END
GO

-- display_name/preferences as a later ALTER, not in the CREATE TABLE above, so a database
-- that already ran an earlier version of this file still ends up with them.
IF COL_LENGTH('dbo.users', 'display_name') IS NULL
  ALTER TABLE dbo.users ADD display_name NVARCHAR(255) NULL;
GO

IF COL_LENGTH('dbo.users', 'preferences') IS NULL
  ALTER TABLE dbo.users ADD preferences NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'ck_users_preferences_json')
  ALTER TABLE dbo.users ADD CONSTRAINT ck_users_preferences_json CHECK (preferences IS NULL OR ISJSON(preferences) = 1);
GO

DROP PROCEDURE IF EXISTS dbo.upsert_user;
GO
DROP PROCEDURE IF EXISTS dbo.filter_users;
GO
DROP PROCEDURE IF EXISTS dbo.update_user_profile;
GO

-- Account management (scripts/create_superuser.py, auth_controller.py's change_password):
-- params = [id, first_name, last_name, email, hashed_password, role, is_active, actor]. NULL
-- leaves a column as it is on update - change_password passes NULL for role, since changing
-- a password never changes what the account is allowed to do. Never touches display_name or
-- preferences - see update_user_profile for those.
CREATE PROCEDURE dbo.upsert_user(
  @p_id INT,
  @p_first_name NVARCHAR(255),
  @p_last_name NVARCHAR(255),
  @p_email NVARCHAR(255),
  @p_hashed_password NVARCHAR(255),
  @p_role NVARCHAR(20),
  @p_is_active BIT,
  @p_actor NVARCHAR(255)
)
AS
BEGIN
  SET NOCOUNT ON;
  IF @p_id IS NULL
  BEGIN
    INSERT INTO dbo.users (first_name, last_name, email, hashed_password, role, is_active, created_by, created_on)
    VALUES (@p_first_name, @p_last_name, @p_email, @p_hashed_password, COALESCE(@p_role, 'user'), COALESCE(@p_is_active, 1), @p_actor, SYSUTCDATETIME());
    SELECT CAST(SCOPE_IDENTITY() AS INT) AS id;
  END
  ELSE
  BEGIN
    UPDATE dbo.users
    SET first_name = COALESCE(@p_first_name, first_name),
        last_name = COALESCE(@p_last_name, last_name),
        email = COALESCE(@p_email, email),
        hashed_password = COALESCE(@p_hashed_password, hashed_password),
        role = COALESCE(@p_role, role),
        is_active = COALESCE(@p_is_active, is_active),
        updated_by = @p_actor,
        updated_on = SYSUTCDATETIME()
    WHERE id = @p_id;
    SELECT @p_id AS id;
  END
END
GO

-- app/dependencies.py: [None, None, None, email, None, None, None, None] (resolve the
-- caller of a login token). app/controllers/auth_controller.py: the same shape for
-- authenticate_user, and [user_id, None, None, None, None, None, 1, 1] for
-- change_password. Unlike every other filter_* procedure, @p_email matches exactly, not
-- by substring: this is an identity lookup, not a search box, and a LIKE match would let
-- "alice@example.com" resolve "malicious-alice@example.com.evil.test" too. One result
-- set, no total_records - every caller reads result[0][0], never page_of().
CREATE PROCEDURE dbo.filter_users(
  @p_id INT,
  @p_first_name NVARCHAR(255),
  @p_last_name NVARCHAR(255),
  @p_email NVARCHAR(255),
  @p_role NVARCHAR(20),
  @p_is_active BIT,
  @p_page_number INT,
  @p_page_size INT
)
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @v_limit INT = COALESCE(@p_page_size, 100);
  DECLARE @v_offset INT = (COALESCE(@p_page_number, 1) - 1) * @v_limit;

  SELECT id, first_name, last_name, email, hashed_password, role, display_name, preferences,
         is_active, created_by, created_on, updated_by, updated_on
  FROM dbo.users
  WHERE (@p_id IS NULL OR id = @p_id)
    AND (@p_first_name IS NULL OR first_name LIKE '%' + @p_first_name + '%')
    AND (@p_last_name IS NULL OR last_name LIKE '%' + @p_last_name + '%')
    AND (@p_email IS NULL OR email = @p_email)
    AND (@p_role IS NULL OR role = @p_role)
    AND (@p_is_active IS NULL OR is_active = @p_is_active)
  ORDER BY created_on DESC
  OFFSET @v_offset ROWS FETCH NEXT @v_limit ROWS ONLY;
END
GO

-- PATCH /chat/me (self-service): params = [email, display_name, preferences]. NULL leaves a
-- column as it is - the same rule as every upsert here - but an empty string display_name
-- clears the name, and '{}' clears the preferences, since PATCH /chat/me is the only writer
-- and both are how it asks for a clear (app/logic/user_context.py: clean_preferences).
CREATE PROCEDURE dbo.update_user_profile(
  @p_email NVARCHAR(255),
  @p_display_name NVARCHAR(255),
  @p_preferences NVARCHAR(MAX)
)
AS
BEGIN
  SET NOCOUNT ON;
  UPDATE dbo.users
  SET display_name = CASE WHEN @p_display_name IS NULL THEN display_name ELSE NULLIF(@p_display_name, '') END,
      preferences = CASE WHEN @p_preferences IS NULL THEN preferences ELSE NULLIF(@p_preferences, '{}') END,
      updated_on = SYSUTCDATETIME()
  WHERE email = @p_email;

  SELECT id, first_name, last_name, email, hashed_password, role, display_name, preferences,
         is_active, created_by, created_on, updated_by, updated_on
  FROM dbo.users
  WHERE email = @p_email;
END
GO
