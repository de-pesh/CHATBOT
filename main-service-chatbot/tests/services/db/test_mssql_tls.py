"""Encrypt= / TrustServerCertificate= in DB_CONNECTION_STRING become the TLS arguments python-tds needs."""
import pytest
from app.logic.connection_string import connection_parts
from app.services.db import mssql_tls
from app.services.db.mssql_tls import tls_connect_kwargs
from app.services.db.schema_migrator import SchemaError, open_target

BASE = "Server=db,1444;UserID=u;Password=p;Database=d"


def _kwargs(suffix="", ca_file=None):
    return tls_connect_kwargs(connection_parts(BASE + suffix), ca_file)


@pytest.mark.parametrize("suffix", ["", ";Encrypt=no", ";Encrypt=false", ";TrustServerCertificate=yes"])
def test_no_encryption_asked_for_means_a_plain_connection(suffix):
    assert _kwargs(suffix) == {}


def test_encrypt_with_trust_server_certificate_encrypts_without_checking_the_certificate(monkeypatch):
    monkeypatch.setattr(mssql_tls, "_patched", True)  # the context patch itself is exercised in the live test
    kwargs = _kwargs(";Encrypt=yes;TrustServerCertificate=yes")
    assert kwargs["cafile"] and kwargs["validate_host"] is False


@pytest.mark.parametrize("word", ["yes", "true", "mandatory", "strict", "YES"])
def test_every_spelling_of_yes_turns_encryption_on(word, monkeypatch):
    monkeypatch.setattr(mssql_tls, "_patched", True)
    assert _kwargs(f";Encrypt={word};TrustServerCertificate=true")["cafile"]


def test_a_ca_file_is_used_to_check_the_certificate_chain():
    assert _kwargs(";Encrypt=yes", ca_file="/etc/ssl/db-ca.pem") == {"cafile": "/etc/ssl/db-ca.pem", "validate_host": False}


def test_encrypt_alone_is_refused_rather_than_silently_unverified_or_unencrypted():
    with pytest.raises(ValueError, match="TrustServerCertificate=yes"):
        _kwargs(";Encrypt=yes")


def test_the_schema_step_reports_it_as_a_schema_error_naming_the_fix():
    with pytest.raises(SchemaError, match="DB_TLS_CA_FILE"):
        open_target(BASE + ";Encrypt=yes", 5, 5)


def test_the_context_patch_builds_a_context_that_accepts_any_certificate(monkeypatch):
    import OpenSSL
    import pytds.tls

    original = pytds.tls.create_context
    monkeypatch.setattr(mssql_tls, "_patched", False)
    try:
        mssql_tls._trust_any_certificate()
        context = pytds.tls.create_context("ignored")
        assert context.get_verify_mode() == OpenSSL.SSL.VERIFY_NONE
        mssql_tls._trust_any_certificate()  # idempotent
    finally:
        pytds.tls.create_context = original
        mssql_tls._patched = False
