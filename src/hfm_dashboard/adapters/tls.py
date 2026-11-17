"""TLS verification for HTTP adapters.

httpx verifies against its bundled certifi list by default, which does not contain a corporate
CA, so every internal HTTPS endpoint would fail. With certificate validation on, the operating
system's trust store (Windows certificate store on the monitoring server) is used instead.
"""

from __future__ import annotations

import ssl

import truststore


def verify_setting(cert_validation: bool) -> ssl.SSLContext | bool:
    """Value for httpx's ``verify``: an OS-trust-store context, or False when validation is off."""
    if not cert_validation:
        return False
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
