"""TLS for the SQL Server connection, from the `Encrypt=` and `TrustServerCertificate=` keys of DB_CONNECTION_STRING.

python-tds only encrypts when it is given a CA file, and then always verifies the server's certificate against it, so
`Encrypt=yes;TrustServerCertificate=yes` (what an ODBC/ADO.NET connection string for an encrypted server looks like) needs
one thing more: a context that accepts the server's certificate. That is what `_trust_any_certificate` installs, for exactly
the case the string asks for it.

  Encrypt=no / absent                        plain connection (a server that does not force encryption accepts it)
  Encrypt=yes;TrustServerCertificate=yes     encrypted, the server's certificate is NOT checked
  Encrypt=yes + DB_TLS_CA_FILE               encrypted, the certificate chain is checked against that CA bundle (PEM)
  Encrypt=yes alone                          refused at startup with a message: there is nothing to check the certificate with

The host name in the certificate is never compared (servers are usually addressed by IP here). TLS 1.2 only: that is what
python-tds speaks.
"""
import logging

logger = logging.getLogger(__name__)

_YES = ("yes", "true", "mandatory", "strict", "1")
_TRUST_ANY = "trust-server-certificate"  # stands in for a CA file name; the patched context below never reads it
_patched = False


def wants_tls(options: dict) -> bool:
    return options.get("encrypt", "").lower() in _YES


def tls_connect_kwargs(options: dict, ca_file: str | None = None) -> dict:
    """The extra keyword arguments pytds.connect needs for the TLS the connection string asks for ({} for none).

    `options` are the raw key/value pairs of the string (logic.connection_string.connection_parts); `ca_file` is
    Settings.DB_TLS_CA_FILE. Raises ValueError when encryption is asked for but cannot be done safely."""
    if not wants_tls(options):
        return {}
    if ca_file:
        return {"cafile": ca_file, "validate_host": False}
    if options.get("trustservercertificate", "").lower() in _YES:
        _trust_any_certificate()
        logger.warning("Encrypted SQL Server connection WITHOUT verifying the server's certificate (TrustServerCertificate=yes).")
        return {"cafile": _TRUST_ANY, "validate_host": False}
    raise ValueError(
        "DB_CONNECTION_STRING asks for Encrypt=yes, but there is nothing to verify the server's certificate with. "
        "Add TrustServerCertificate=yes to accept it without checking, or set DB_TLS_CA_FILE to the CA bundle (PEM) "
        "that signed it."
    )


def _trust_any_certificate() -> None:
    """Makes python-tds build a TLS context that accepts any server certificate. Idempotent. Only reached when the
    connection string says TrustServerCertificate=yes."""
    global _patched
    if _patched:
        return
    try:
        import OpenSSL
        import pytds.tls
    except ImportError as error:  # pyOpenSSL is what python-tds drives TLS with
        raise ValueError("Encrypt=yes needs the pyOpenSSL package, which is not installed.") from error

    def create_context(cafile):
        context = OpenSSL.SSL.Context(OpenSSL.SSL.TLSv1_2_METHOD)
        context.set_options(OpenSSL.SSL.OP_NO_SSLv2)
        context.set_options(OpenSSL.SSL.OP_NO_SSLv3)
        context.set_verify(OpenSSL.SSL.VERIFY_NONE, lambda conn, cert, err_num, err_depth, ret_code: True)
        return context

    pytds.tls.create_context = create_context
    _patched = True
