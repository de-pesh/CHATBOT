"""What the chat prompts may know about the caller, as data - and the shape of the migrations
that back dbo.users.role/display_name/preferences and dbo.chatbot_session/history.owner_id."""
from pathlib import Path

import pytest
from app.logic.sql_scripts import split_statements
from app.logic.user_context import (
    MAX_PREFERENCE_VALUE_CHARS,
    MAX_PREFERENCES,
    clean_preferences,
    preferences_of,
    recent_questions_of,
    recent_questions_params,
    update_profile_params,
    user_context,
)


def test_preferences_keep_only_short_plain_text_and_numbers():
    raw = {"role": " Auditor ", "depth": 3, "ratio": 0.5, "flag": True, "nested": {"a": 1}, "list": [1], "": "x", "blank": "  "}
    assert clean_preferences(raw) == {"role": "Auditor", "depth": "3", "ratio": "0.5"}
    assert clean_preferences(None) == clean_preferences("text") == clean_preferences([1]) == {}


def test_preferences_are_capped_in_count_and_length_not_refused():
    many = {f"k{i}": "v" for i in range(MAX_PREFERENCES + 5)}
    assert len(clean_preferences(many)) == MAX_PREFERENCES
    assert len(clean_preferences({"k": "v" * 1000})["k"]) == MAX_PREFERENCE_VALUE_CHARS


@pytest.mark.parametrize("stored,expected", [
    ('{"role": "Auditor", "depth": 3}', {"role": "Auditor", "depth": "3"}),
    (None, {}),
    ("not json", {}),
    ("[1, 2]", {}),
    ('"text"', {}),
])
def test_preferences_of_parses_the_stored_json_string(stored, expected):
    assert preferences_of(stored) == expected


def test_update_profile_params_are_email_name_then_preferences_json():
    assert update_profile_params("a@b.c", "Dee", {"role": "Auditor"}) == ["a@b.c", "Dee", '{"role": "Auditor"}']
    assert update_profile_params("a@b.c", None, None) == ["a@b.c", None, None]


def test_recent_questions_params_are_a_history_filter_scoped_to_the_caller():
    assert recent_questions_params("a@b.c", 5) == [None, None, None, None, None, "a@b.c", True, 1, 5, None]
    assert recent_questions_params("a@b.c", 5, 7)[-1] == 7


def test_recent_questions_are_flattened_cut_and_blanks_dropped():
    rows = [{"prompt": "what is\n  X?"}, {"prompt": ""}, {"prompt": None}, {"prompt": "y" * 50}]
    assert recent_questions_of([rows, [{"total_records": 4}]], 10) == ["what is X?", "y" * 10]
    assert recent_questions_of([], 10) == [] and recent_questions_of(None, 10) == []


def test_the_context_is_what_the_model_may_know():
    context = user_context("Deepesh", {"role": "Auditor"}, ["earlier question"])
    assert context == {"name": "Deepesh", "preferences": {"role": "Auditor"}, "recent_questions": ["earlier question"]}


def test_there_is_no_context_when_there_is_nothing_to_say():
    assert user_context(None, None) is None
    assert user_context(None, {}, []) is None


# ------------------------------------------------------------------ the migrations

USERS_SQL = Path(__file__).resolve().parents[2] / "scripts" / "mssql" / "05_users.sql"


def test_users_has_the_columns_the_code_reads():
    text = USERS_SQL.read_text(encoding="utf-8")
    for column in ("role", "display_name", "preferences"):
        assert column in text


def test_the_procedures_take_the_parameters_the_code_sends():
    text = USERS_SQL.read_text(encoding="utf-8")
    upsert = text.split("CREATE PROCEDURE dbo.upsert_user(")[1].split(")\nAS")[0]
    filter_ = text.split("CREATE PROCEDURE dbo.filter_users(")[1].split(")\nAS")[0]
    profile = text.split("CREATE PROCEDURE dbo.update_user_profile(")[1].split(")\nAS")[0]
    assert [line.split()[0] for line in upsert.strip().splitlines()] == [
        "@p_id", "@p_first_name", "@p_last_name", "@p_email", "@p_hashed_password", "@p_role", "@p_is_active", "@p_actor",
    ]
    assert [line.split()[0] for line in filter_.strip().splitlines()] == [
        "@p_id", "@p_first_name", "@p_last_name", "@p_email", "@p_role", "@p_is_active", "@p_page_number", "@p_page_size",
    ]
    assert [line.split()[0] for line in profile.strip().splitlines()] == ["@p_email", "@p_display_name", "@p_preferences"]


LINKS_SQL = Path(__file__).resolve().parents[2] / "scripts" / "mssql" / "04_chat_user_links.sql"


def test_the_links_migration_adds_the_column_before_anything_uses_it():
    batches = split_statements(LINKS_SQL.read_text(encoding="utf-8"))
    first_use = next(i for i, b in enumerate(batches) if "SET owner_id" in b or "CREATE PROCEDURE" in b)
    added = [i for i, b in enumerate(batches) if "ADD owner_id" in b]
    assert len(added) == 2 and max(added) < first_use, "a new column is only visible to the batches after the one that adds it"


def test_the_links_migration_redefines_the_four_procedures_with_owner_id_last_and_optional():
    text = LINKS_SQL.read_text(encoding="utf-8")
    for proc in ("upsert_chatbot_session", "filter_chatbot_sessions", "upsert_chatbot_history", "filter_chatbot_history"):
        params = text.split(f"CREATE PROCEDURE dbo.{proc}(")[1].split(")\nAS")[0].strip().splitlines()
        assert params[-1].strip() == "@p_owner_id INT = NULL", proc


def test_a_row_linked_to_another_owner_is_hidden_and_an_unlinked_one_is_not():
    text = LINKS_SQL.read_text(encoding="utf-8")
    rule = "(@p_owner_id IS NULL OR owner_id IS NULL OR owner_id = @p_owner_id)"
    assert text.count(rule) == 4, "both selects of both filter procedures"


def test_the_chat_user_migration_only_cleans_up_now():
    text = (Path(LINKS_SQL.parent) / "03_chat_user.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE" not in text and "CREATE PROCEDURE" not in text
    assert "DROP TABLE IF EXISTS dbo.chat_user" in text
