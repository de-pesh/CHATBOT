"""Splitting a T-SQL script into executable batches."""
from app.logic.sql_scripts import split_statements


def test_go_ends_a_batch_in_any_case_and_only_on_a_line_of_its_own():
    assert split_statements("A\nGO\nB\ngo\n  GO  \nC") == ["A", "B", "C"]
    assert split_statements("SELECT 'GO'\nFROM t") == ["SELECT 'GO'\nFROM t"]


def test_a_batch_that_is_only_comments_or_blank_is_dropped():
    assert split_statements("-- nothing\nGO\n\nGO\nSELECT 1") == ["SELECT 1"]
