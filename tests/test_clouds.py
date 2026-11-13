"""The Azure cloud table: endpoints, URL trust, detection and FreeRDP options.

No network and no credentials — this is all pure table lookup and string work.
"""
import pytest

from avd_feed_connect import clouds


def test_every_cloud_is_complete():
    for key, cloud in clouds.CLOUDS.items():
        assert cloud.key == key, "the key must match its table slot"
        assert cloud.label and cloud.login_host and cloud.discovery
        assert cloud.avd_resource.startswith("https://")
        assert cloud.discovery.startswith("https://")
        assert cloud.trusted_suffixes


def test_order_covers_the_table_and_leads_with_the_default():
    assert set(clouds.ORDER) == set(clouds.CLOUDS)
    assert clouds.ORDER[0] == clouds.DEFAULT_KEY
    assert len(clouds.LABELS) == len(clouds.ORDER)


def test_default_is_commercial():
    assert clouds.DEFAULT.key == "commercial"
    assert clouds.DEFAULT.login_host == "login.microsoftonline.com"
    assert "wvd.microsoft.com" in clouds.DEFAULT.scope


def test_usgov_endpoints():
    gov = clouds.CLOUDS["usgov"]
    assert gov.login_host == "login.microsoftonline.us"
    assert gov.scope.startswith("https://www.wvd.azure.us/.default")
    assert gov.discovery == "https://rdweb.wvd.azure.us/api/arm/feeddiscovery"
    assert gov.login("organizations") == \
        "https://login.microsoftonline.us/organizations/oauth2/v2.0"


def test_scope_carries_the_refresh_and_identity_scopes():
    for cloud in clouds.CLOUDS.values():
        for needed in ("offline_access", "openid", "profile"):
            assert needed in cloud.scope


@pytest.mark.parametrize("key,expected", [
    ("usgov", "usgov"),
    ("USGov", "usgov"),          # case-insensitive
    ("  usgov  ", "usgov"),      # tolerant of stray whitespace
    ("commercial", "commercial"),
    ("nonsense", "commercial"),  # unknown must fall back, never raise
    ("", "commercial"),
    (None, "commercial"),
])
def test_resolve(key, expected, monkeypatch):
    monkeypatch.delenv("AVD_CLOUD", raising=False)
    assert clouds.resolve(key).key == expected


def test_resolve_honours_the_environment(monkeypatch):
    monkeypatch.setenv("AVD_CLOUD", "usgov")
    assert clouds.resolve().key == "usgov"
    # An explicit argument still wins over the environment.
    assert clouds.resolve("commercial").key == "commercial"


def test_resolve_ignores_a_bogus_environment(monkeypatch):
    monkeypatch.setenv("AVD_CLOUD", "atlantis")
    assert clouds.resolve().key == "commercial"


# -- detection --------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "rdgateway-r1.wvd.azure.us",
    "rdweb.wvd.azure.us",
    "rdweb-g-usg-r1.wvd.azure.us",
    "management.usgovcloudapi.net",
])
def test_detects_usgov_hosts(host):
    assert clouds.detect_from_host(host).key == "usgov"


@pytest.mark.parametrize("host", [
    "rdweb.wvd.microsoft.com",       # commercial is the default, not "detected"
    "login.microsoftonline.com",
    "",
    None,
    "wvd.azure.us.evil.io",          # suffix trick must not match
])
def test_detects_nothing_for_other_hosts(host):
    assert clouds.detect_from_host(host) is None


def test_detect_from_url():
    assert clouds.detect_from_url("https://rdgateway-r1.wvd.azure.us/x").key == "usgov"
    assert clouds.detect_from_url("https://rdweb.wvd.microsoft.com/x") is None
    assert clouds.detect_from_url("not a url") is None


# -- URL trust --------------------------------------------------------------

def test_login_hosts_include_the_certauth_host():
    """Entra's certificate-based auth (CAC) runs on a separate certauth host."""
    for cloud in clouds.CLOUDS.values():
        assert cloud.login_host in cloud.login_hosts
        assert "certauth." + cloud.login_host in cloud.login_hosts


@pytest.mark.parametrize("url", [
    "https://login.microsoftonline.us/organizations/oauth2/v2.0/authorize?x=1",
    "https://certauth.login.microsoftonline.us/x",
])
def test_usgov_accepts_its_own_login_urls(url):
    assert clouds.CLOUDS["usgov"].is_login_url(url)


@pytest.mark.parametrize("url", [
    "http://login.microsoftonline.us/x",                  # not https
    "https://login.microsoftonline.us.evil.io/x",         # suffix trick
    "https://login.microsoftonline.us@evil.io/x",         # userinfo trick
    "https://evil.io/login.microsoftonline.us",           # host in path
    "https://login.microsoftonline.com/x",                # the OTHER cloud
    "https://sub.login.microsoftonline.us/x",             # not an exact host
    "", "javascript:alert(1)",
])
def test_usgov_rejects_everything_else(url):
    assert not clouds.CLOUDS["usgov"].is_login_url(url)


def test_trusted_urls_are_scoped_per_cloud():
    gov, com = clouds.CLOUDS["usgov"], clouds.DEFAULT
    assert gov.is_trusted_url("https://rdweb.wvd.azure.us/x")
    # A commercial account's feed can never legitimately name a gov host.
    assert not com.is_trusted_url("https://rdweb.wvd.azure.us/x")
    # Shared Microsoft hosts stay trusted in both.
    for cloud in (gov, com):
        assert cloud.is_trusted_url("https://rdweb.wvd.microsoft.com/x")


@pytest.mark.parametrize("url", [
    "https://anything.mil/x",            # never trust a whole TLD
    "https://evil.mil/x",
    "https://rdweb.wvd.azure.us.evil.io/x",
    "http://rdweb.wvd.azure.us/x",
    "https://evilazure.us/x",            # no dot boundary
])
def test_usgov_never_trusts_these(url):
    assert not clouds.CLOUDS["usgov"].is_trusted_url(url)


# -- FreeRDP /azure: --------------------------------------------------------

def test_commercial_needs_no_freerdp_override():
    assert clouds.DEFAULT.freerdp_azure_option("some-tenant") is None


GUID = "fae6d70f-954b-4811-92b6-0530d6f84c43"


def test_usgov_freerdp_option():
    option = clouds.CLOUDS["usgov"].freerdp_azure_option(GUID)
    assert option.startswith("/azure:")
    assert "ad:login.microsoftonline.us" in option
    assert f"tenantid:{GUID}" in option
    assert "use-tenantid" in option
    # Scope must be encoded exactly once: %3A, not %253A.
    assert "avd-scope:https%3A%2F%2Fwww.wvd.azure.us%2F.default" in option
    assert "%253A" not in option
    # Commas separate FreeRDP sub-options, so none may survive inside a value.
    assert option.count(",") == 4


def test_usgov_freerdp_option_without_a_tenant():
    """A .rdp with no aadtenantid must still get the authority and scope."""
    option = clouds.CLOUDS["usgov"].freerdp_azure_option(None)
    assert "ad:login.microsoftonline.us" in option
    assert "avd-scope:" in option
    assert "tenantid" not in option


def test_freerdp_option_omits_ad_when_no_host_is_configured():
    """The fallback if ad: ever moves FreeRDP's redirect somewhere unusable."""
    import dataclasses
    gov = dataclasses.replace(clouds.CLOUDS["usgov"], freerdp_ad_host=None)
    option = gov.freerdp_azure_option(GUID)
    assert "ad:" not in option
    assert f"tenantid:{GUID}" in option
    assert "avd-scope:" in option


def test_a_non_guid_tenant_id_is_refused():
    """The value comes from the feed's .rdp, and a comma would start a new
    FreeRDP sub-option — so anything but a GUID is dropped."""
    option = clouds.CLOUDS["usgov"].freerdp_azure_option("x,auth-helper:/bin/sh")
    assert "tenantid" not in option
    assert "auth-helper" not in option
    assert "ad:login.microsoftonline.us" in option


@pytest.mark.parametrize("url", [
    # Customers choose the leftmost labels under these suffixes and get valid
    # Microsoft TLS certs, so they must never receive the bearer token.
    "https://rdwebassets.blob.core.usgovcloudapi.net/icon.png",
    "https://attacker.file.core.usgovcloudapi.net/x",
    "https://attacker.eastus.cloudapp.usgovcloudapi.net/x",
    "https://attacker.database.usgovcloudapi.net/x",
    "https://attacker.vault.usgovcloudapi.net/x",
    "https://attacker.azure.us/x",
])
def test_usgov_does_not_trust_customer_registrable_hosts(url):
    assert not clouds.CLOUDS["usgov"].is_trusted_url(url)


@pytest.mark.parametrize("url", [
    "https://rdweb.wvd.azure.us/api/arm/feeddiscovery",
    "https://rdweb-g-usg-r1.wvd.azure.us/api/arm/hubdiscovery",
    "https://rdgateway-r1.wvd.azure.us/x",
    "https://login.microsoftonline.us/common/oauth2/v2.0/token",
])
def test_usgov_still_trusts_the_hosts_it_actually_uses(url):
    assert clouds.CLOUDS["usgov"].is_trusted_url(url)


def test_freerdp_redirect_is_forced_to_the_registered_uri():
    """Overriding ad: for a sovereign cloud also moves FreeRDP's derived
    redirect to https://login.microsoftonline.us/<tenant>/oauth2/nativeclient,
    which is NOT registered (AADSTS50011). It has to be forced back."""
    option = clouds.CLOUDS["usgov"].freerdp_azure_option(GUID)
    assert "avd-access:" in option
    # FreeRDP printf-expands the value, so literal percents must be doubled;
    # it emits single-percent encoding at runtime. Verified against the real
    # binary: redirect_uri=https%3A%2F%2Flogin.microsoftonline.com%2Fcommon%2F...
    assert "avd-access:https%%3A%%2F%%2Flogin.microsoftonline.com%%2Fcommon%%2Foauth2%%2Fnativeclient" in option
    assert "login.microsoftonline.us%%2Fcommon" not in option


def test_the_registered_redirect_is_shared_with_our_own_flow():
    from avd_feed_connect import config
    assert config.REDIRECT == clouds.NATIVECLIENT_REDIRECT
