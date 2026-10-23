"""The one DB_CONNECTION_STRING parser behind the MSSQL service and the schema migrator."""
import pytest
from app.logic.connection_string import connection_parts, parse_connection_string


def test_the_documented_mssql_shape_with_host_and_port_in_server():
    assert parse_connection_string("Server=host,1433;UserID=sa;Password=p;Database=chatbot", required=True) == {
        "host": "host", "port": 1433, "user": "sa", "password": "p", "database": "chatbot",
    }


def test_a_separate_port_key():
    assert parse_connection_string("Server=db;Port=3307;UserID=app;Password=pw;Database=chatbot") == {
        "host": "db", "port": 3307, "user": "app", "password": "pw", "database": "chatbot",
    }


def test_a_port_named_in_server_wins_over_a_port_key():
    assert parse_connection_string("Server=db,1444;Port=9;UserID=u;Password=p;Database=d")["port"] == 1444


def test_no_port_at_all_is_left_for_the_backend_to_default():
    assert parse_connection_string("Server=db;UserID=u;Password=p;Database=d")["port"] is None


@pytest.mark.parametrize("user_key", ["UserID", "Uid", "User Id", "User", "userid"])
@pytest.mark.parametrize("password_key", ["Password", "Pwd"])
@pytest.mark.parametrize("db_key", ["Database", "Initial Catalog"])
@pytest.mark.parametrize("host_key", ["Server", "Data Source"])
def test_every_alias_is_honoured(host_key, db_key, password_key, user_key):
    parsed = parse_connection_string(f"{host_key}=h;{user_key}=u;{password_key}=p;{db_key}=d", required=True)
    assert (parsed["host"], parsed["user"], parsed["password"], parsed["database"]) == ("h", "u", "p", "d")


def test_spacing_and_case_around_the_pairs_are_not_meaningful():
    assert parse_connection_string(" server = db , 1444 ; UID = u ; PWD = secret ; initial catalog = x ; ") == {
        "host": "db", "port": 1444, "user": "u", "password": "secret", "database": "x",
    }


def test_missing_parts_fall_back_to_local_defaults_unless_required():
    assert parse_connection_string("Database=chatbot") == {
        "host": "localhost", "port": None, "user": "root", "password": "", "database": "chatbot",
    }
    assert parse_connection_string("") == {
        "host": "localhost", "port": None, "user": "root", "password": "", "database": "",
    }


def test_an_empty_password_is_a_password_not_a_missing_one():
    parsed = parse_connection_string("Server=h;UserID=u;Password=;Database=d", required=True)
    assert parsed["password"] == ""


def test_required_names_every_missing_part_in_the_same_message_as_before():
    with pytest.raises(ValueError) as error:
        parse_connection_string("Server=h;UserID=u", required=True)
    assert str(error.value) == (
        "DB_CONNECTION_STRING is missing required part(s): Password/Pwd, Database/Initial Catalog. "
        "Expected 'Server=host,port;UserID=user;Password=pass;Database=db' - "
        "keys are case-insensitive and extra whitespace around ';' is fine."
    )
    with pytest.raises(ValueError, match=r"UserID/Uid, Password/Pwd, Database/Initial Catalog"):
        parse_connection_string("Server=h", required=True)


def test_pairs_without_an_equals_sign_or_a_key_are_ignored():
    assert connection_parts("garbage;;=x;a=1;B = two=three") == {"a": "1", "b": "two=three"}


def test_backend_specific_keys_are_left_for_the_backend_to_read():
    parts = connection_parts("Server=h;SslMode=Required;Encrypt=yes;TrustServerCertificate=true")
    assert (parts["sslmode"], parts["encrypt"], parts["trustservercertificate"]) == ("Required", "yes", "true")
