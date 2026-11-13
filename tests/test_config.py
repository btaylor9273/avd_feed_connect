"""Only the Entra login host may be loaded for FreeRDP's "Browse to:" prompt."""

import pytest

from avd_feed_connect import clouds, config


def test_accepts_entra_login():
    assert config.is_aad_login_url(
        "https://login.microsoftonline.com/common/oauth2/authorize?client_id=x")


@pytest.mark.parametrize("url", [
    "http://login.microsoftonline.com/x",
    "https://evil.example/x",
    "https://login.microsoftonline.com.evil.io/x",
    "https://evil.io/login.microsoftonline.com",
    "https://login.microsoftonline.com@evil.io/x",
    "https://microsoftonline.com/x",
    "file:///etc/passwd", "javascript:alert(1)", "",
])
def test_rejects_others(url):
    assert not config.is_aad_login_url(url)


# -- per-cloud behaviour ----------------------------------------------------
# The module-level predicates default to the process-wide cloud (commercial
# above); passing a cloud scopes them to one account's environment.

def test_accepts_the_certauth_host():
    """Certificate-based auth (CAC) runs on a separate certauth host, so
    refusing it would break sign-in exactly when the card is requested."""
    assert config.is_aad_login_url(
        "https://certauth.login.microsoftonline.com/x")


def test_accepts_a_sovereign_login_host_for_that_cloud():
    gov = clouds.CLOUDS["usgov"]
    for url in ("https://login.microsoftonline.us/organizations/oauth2/v2.0/authorize",
                "https://certauth.login.microsoftonline.us/x"):
        assert config.is_aad_login_url(url, gov)
        # ...and not for a commercial account.
        assert not config.is_aad_login_url(url, clouds.DEFAULT)


@pytest.mark.parametrize("url", [
    "https://login.microsoftonline.us.evil.io/x",
    "https://certauth.login.microsoftonline.us@evil.io/x",
    "http://login.microsoftonline.us/x",
    "https://sub.login.microsoftonline.us/x",
])
def test_rejects_sovereign_lookalikes(url):
    assert not config.is_aad_login_url(url, clouds.CLOUDS["usgov"])
