-- dbo.chat_user used to be a separate "who is chatting" table, keyed by
-- (tenant, entity, user) claims from a login token, so a conversation could
-- be scoped without a row in a local users table at all. Now that real login
-- exists (05_users.sql) and dbo.users.email is globally unique - no more
-- per-tenant duplicates of one email - it was pure duplication of "who is
-- this," so it is gone: chatbot_session/chatbot_history now point at
-- dbo.users.id directly (owner_id, added in 04_chat_user_links.sql) and the
-- profile fields it held (display_name, preferences) moved onto dbo.users.
--
-- This file's only job now is to remove it - and what 04_chat_user_links.sql
-- used to add to point at it - from a database that already has them: a
-- forward-only migration undoing what these two files used to create, for
-- anyone who already ran that version. On a fresh database every step below
-- is a no-op (IF EXISTS / IF COL_LENGTH... IS NOT NULL).

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_session_chat_user' AND object_id = OBJECT_ID('dbo.chatbot_session'))
  DROP INDEX ix_chatbot_session_chat_user ON dbo.chatbot_session;
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_chatbot_history_chat_user' AND object_id = OBJECT_ID('dbo.chatbot_history'))
  DROP INDEX ix_chatbot_history_chat_user ON dbo.chatbot_history;
GO
IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_chatbot_session_chat_user')
  ALTER TABLE dbo.chatbot_session DROP CONSTRAINT fk_chatbot_session_chat_user;
GO
IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_chatbot_history_chat_user')
  ALTER TABLE dbo.chatbot_history DROP CONSTRAINT fk_chatbot_history_chat_user;
GO
IF COL_LENGTH('dbo.chatbot_session', 'chat_user_id') IS NOT NULL
  ALTER TABLE dbo.chatbot_session DROP COLUMN chat_user_id;
GO
IF COL_LENGTH('dbo.chatbot_history', 'chat_user_id') IS NOT NULL
  ALTER TABLE dbo.chatbot_history DROP COLUMN chat_user_id;
GO

DROP PROCEDURE IF EXISTS dbo.upsert_chat_user;
GO
DROP PROCEDURE IF EXISTS dbo.update_chat_user;
GO
DROP TABLE IF EXISTS dbo.chat_user;
GO
