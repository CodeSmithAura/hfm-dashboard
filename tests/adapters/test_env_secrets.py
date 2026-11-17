"""Environment-variable secret store and the in-memory fake."""

from __future__ import annotations

import pytest

from hfm_dashboard.adapters.env_secrets import EnvironmentSecretStore, FakeSecretStore, variable_prefix
from hfm_dashboard.ports.secrets import SecretNotFound


def test_variable_prefix():
    assert variable_prefix("hfm-mon/PROD/weblogic") == "HFM_MON_PROD_WEBLOGIC"


def test_reads_username_and_password():
    store = EnvironmentSecretStore({"HFM_MON_NONPROD_WEBLOGIC_USERNAME": "monitor-user",
                                    "HFM_MON_NONPROD_WEBLOGIC_PASSWORD": "pw-not-real"})
    secret = store.get("hfm-mon/NONPROD/weblogic")
    assert (secret.username, secret.password) == ("monitor-user", "pw-not-real")
    assert "pw-not-real" not in repr(secret)


@pytest.mark.parametrize("env", [{}, {"HFM_MON_X_USERNAME": "user-value-1"},
                                 {"HFM_MON_X_PASSWORD": "password-value-1"}])
def test_missing_names_the_variables_not_values(env):
    with pytest.raises(SecretNotFound) as info:
        EnvironmentSecretStore(env).get("hfm-mon/x")
    msg = str(info.value)
    assert "HFM_MON_X_USERNAME" in msg and "HFM_MON_X_PASSWORD" in msg
    assert "user-value-1" not in msg and "password-value-1" not in msg


def test_fake_store():
    store = FakeSecretStore({"a": ("u", "p")})
    assert store.get("a").username == "u"
    with pytest.raises(SecretNotFound):
        store.get("b")
