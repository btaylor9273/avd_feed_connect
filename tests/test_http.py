"""The bearer token is only ever sent to https Microsoft hosts."""

import pytest

from avd_feed_connect import clouds, config, http
from avd_feed_connect.gui import storage


@pytest.mark.parametrize("url", [
    "https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "https://microsoft.com/x",
])
def test_trusted(url):
    assert config.is_trusted_url(url)


@pytest.mark.parametrize("url", [
    "http://rdweb.wvd.microsoft.com/x",            # not https
    "https://evilmicrosoft.com/x",                  # no dot boundary
    "https://microsoft.com.evil.io/x",              # suffix trick
    "https://evil.io/rdweb.wvd.microsoft.com",      # host in path
    "https://rdweb.wvd.microsoft.com@evil.io/x",    # userinfo trick
    "https://evil.io/?h=login.microsoftonline.com",
    "ftp://rdweb.wvd.microsoft.com/x",
    "", "not a url",
])
def test_untrusted(url):
    assert not config.is_trusted_url(url)


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("request must not be sent")
    monkeypatch.setattr(http.urllib.request, "urlopen", boom)


def test_get_refuses_untrusted(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(ValueError):
        http.get("https://evil.example/x", "secret-token")


def test_bearer_bytes_refuses_untrusted(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(ValueError):
        storage.bearer_bytes("https://evil.example/icon.png", "secret-token")


# -- per-cloud token trust --------------------------------------------------

def test_sovereign_hosts_are_trusted_for_a_sovereign_account():
    gov = clouds.CLOUDS["usgov"]
    for url in ("https://rdweb.wvd.azure.us/api/arm/feeddiscovery",
                "https://rdgateway-r1.wvd.azure.us/x",
                "https://login.microsoftonline.us/common/oauth2/v2.0/token"):
        assert config.is_trusted_url(url, gov)


def test_sovereign_hosts_are_not_trusted_for_a_commercial_account():
    """A commercial account's feed can never legitimately name a gov host."""
    assert not config.is_trusted_url("https://rdweb.wvd.azure.us/x", clouds.DEFAULT)


@pytest.mark.parametrize("url", [
    "https://anything.mil/x",                 # never trust a whole TLD
    "https://evil.mil/x",
    "https://rdweb.wvd.azure.us.evil.io/x",   # suffix trick
    "http://rdweb.wvd.azure.us/x",            # not https
    "https://evilazure.us/x",                 # no dot boundary
])
def test_never_trusted_in_any_cloud(url):
    for cloud in clouds.CLOUDS.values():
        assert not config.is_trusted_url(url, cloud)


def test_get_refuses_a_gov_url_for_a_commercial_account(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(ValueError):
        http.get("https://rdweb.wvd.azure.us/x", "secret-token", cloud=clouds.DEFAULT)


def test_get_allows_a_gov_url_for_a_gov_account(monkeypatch):
    """The same URL must pass the trust check for the account it belongs to."""
    sent = {}

    class Response:
        status = 200
        headers = {}

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def capture(req, timeout=None):
        sent["url"] = req.full_url
        return Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", capture)
    status, _headers, body = http.get("https://rdweb.wvd.azure.us/x", "secret-token",
                                      cloud=clouds.CLOUDS["usgov"])
    assert (status, body) == (200, "ok")
    assert sent["url"] == "https://rdweb.wvd.azure.us/x"
