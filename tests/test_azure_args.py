"""FreeRDP sovereign-cloud (/azure:) argv construction.

No network and no FreeRDP: these build a .rdp on disk and assert on the
argument list. The fixtures keep the feed's real ``redirectsmartcards``
property to document that the app no longer acts on it.
"""
import pytest

from avd_feed_connect import clouds
from avd_feed_connect.rdp.launcher import build_argv, build_azure_args
from avd_feed_connect.rdp.security import resolve_security

TENANT = "00000000-1111-2222-3333-444444444444"

# Trimmed from a real US Army Azure Government feed .rdp.
GOV_RDP = f"""full address:s:rdgateway-r1.wvd.azure.us
gatewayhostname:s:rdgateway-r1.wvd.azure.us:443
geo:s:USG
resourceprovider:s:arm
aadtenantid:s:{TENANT}
enablerdsaadauth:i:1
targetisaadjoined:i:1
redirectsmartcards:i:1
"""

COMMERCIAL_RDP = """full address:s:rdgateway-r1.wvd.microsoft.com
aadtenantid:s:11111111-2222-3333-4444-555555555555
enablerdsaadauth:i:1
redirectsmartcards:i:0
"""

NLA_RDP = """full address:s:host.example.com
enablerdsaadauth:i:0
"""


def write(tmp_path, body, name="desktop.rdp"):
    path = tmp_path / name
    path.write_text(body)
    return str(path)


# -- build_azure_args -------------------------------------------------------

def test_gov_aad_connection_gets_one_azure_option(tmp_path):
    decision = resolve_security(write(tmp_path, GOV_RDP))
    args = build_azure_args(clouds.CLOUDS["usgov"], decision)
    assert len(args) == 1
    option = args[0]
    assert option.startswith("/azure:")
    assert "ad:login.microsoftonline.us" in option
    assert f"tenantid:{TENANT}" in option
    assert "use-tenantid" in option
    assert "avd-scope:https%3A%2F%2Fwww.wvd.azure.us%2F.default" in option
    # The redirect must be forced back to the one registered for this client.
    assert "avd-access:https%%3A%%2F%%2Flogin.microsoftonline.com" in option


def test_scope_is_encoded_exactly_once(tmp_path):
    """Double-encoding (%253A) is the classic failure of this option. Note the
    doubled percents in avd-access are different: FreeRDP printf-expands that
    value, so %% there becomes a single % at runtime."""
    decision = resolve_security(write(tmp_path, GOV_RDP))
    option = build_azure_args(clouds.CLOUDS["usgov"], decision)[0]
    scope = [p for p in option.split(",") if p.startswith("avd-scope:")][0]
    assert "%253A" not in scope and "%252F" not in scope
    assert "%3A" in scope and "%20" in scope


def test_commercial_adds_nothing(tmp_path):
    """The released behaviour must be byte-identical for commercial accounts."""
    decision = resolve_security(write(tmp_path, COMMERCIAL_RDP))
    assert build_azure_args(clouds.DEFAULT, decision) == []


def test_nla_adds_nothing_even_in_a_sovereign_cloud(tmp_path):
    """/azure: only governs the Entra (/sec:aad) path."""
    decision = resolve_security(write(tmp_path, NLA_RDP))
    assert decision.mode == "nla"
    assert build_azure_args(clouds.CLOUDS["usgov"], decision) == []


def test_missing_cloud_adds_nothing(tmp_path):
    decision = resolve_security(write(tmp_path, GOV_RDP))
    assert build_azure_args(None, decision) == []


def test_tenant_id_is_read_from_the_rdp(tmp_path):
    decision = resolve_security(write(tmp_path, GOV_RDP))
    assert decision.aad_tenant_id == TENANT


def test_no_tenant_in_the_rdp_still_sets_authority_and_scope(tmp_path):
    body = GOV_RDP.replace(f"aadtenantid:s:{TENANT}\n", "")
    decision = resolve_security(write(tmp_path, body))
    assert decision.aad_tenant_id == ""
    option = build_azure_args(clouds.CLOUDS["usgov"], decision)[0]
    assert "ad:login.microsoftonline.us" in option
    assert "avd-scope:" in option
    assert "tenantid" not in option


def test_the_azure_option_is_a_single_argv_element(tmp_path):
    """Commas are FreeRDP sub-option separators, not shell separators."""
    decision = resolve_security(write(tmp_path, GOV_RDP))
    args = build_azure_args(clouds.CLOUDS["usgov"], decision)
    assert len(args) == 1 and " " not in args[0]


# -- build_argv (the CLI producer) ------------------------------------------

def test_cli_argv_for_a_gov_desktop(tmp_path):
    argv = build_argv("/app/bin/sdl-freerdp", write(tmp_path, GOV_RDP),
                      "user@example.mil", cloud=clouds.CLOUDS["usgov"])
    assert argv[0] == "/app/bin/sdl-freerdp"
    assert "/sec:aad" in argv
    assert "/gateway:type:arm" in argv
    assert any(a.startswith("/azure:") for a in argv)
    assert "/u:user@example.mil" in argv


def test_cli_argv_for_a_commercial_desktop(tmp_path):
    argv = build_argv("sdl-freerdp", write(tmp_path, COMMERCIAL_RDP), "u@example.com",
                      cloud=clouds.DEFAULT)
    assert not any(a.startswith("/azure:") for a in argv)


def test_cli_argv_still_works_positionally(tmp_path):
    """The pre-existing three-argument call must keep working."""
    argv = build_argv("sdl-freerdp", write(tmp_path, NLA_RDP), "u@example.com")
    assert "/sec:nla" in argv and "/d:" in argv


# -- the tenant id is attacker-influenced data ------------------------------
# It comes from the feed's .rdp file and lands in a FreeRDP option whose
# sub-options are comma-separated, so it is validated as a GUID first.

@pytest.mark.parametrize("hostile", [
    "x,auth-helper:/bin/sh",          # auth-helper is a binary FreeRDP runs
    "x,avd-access:http://evil.io",    # would redirect the authorization code
    "x,ad:login.evil.io",             # would move the authority
    "a,b",
    "00000000-1111-2222-3333-444444444444,auth-helper:/bin/sh",
    "../../etc/passwd",
    "x y", "x\ny", "x\"y", "x;y", "x'y",
    "not-a-guid", "", "   ",
])
def test_a_hostile_tenant_id_cannot_inject_sub_options(tmp_path, hostile):
    body = GOV_RDP.replace(TENANT, hostile)
    decision = resolve_security(write(tmp_path, body))
    option = build_azure_args(clouds.CLOUDS["usgov"], decision)[0]
    sub_options = option[len("/azure:"):].split(",")
    keys = {part.split(":")[0] for part in sub_options}
    assert keys <= {"ad", "tenantid", "use-tenantid", "avd-scope", "avd-access"}, option
    # The hostile value is dropped rather than passed through.
    assert "tenantid" not in keys
    assert "auth-helper" not in option
    # avd-access is present because WE set it — to the registered redirect,
    # never to anything the .rdp supplied.
    access = [p for p in sub_options if p.startswith("avd-access:")]
    assert len(access) == 1
    assert "login.microsoftonline.com" in access[0]
    assert "evil.io" not in option
    # ...and the connection still gets the right authority and scope.
    assert "ad:login.microsoftonline.us" in option
    assert "avd-scope:" in option


def test_a_real_guid_tenant_id_is_used(tmp_path):
    decision = resolve_security(write(tmp_path, GOV_RDP))
    option = build_azure_args(clouds.CLOUDS["usgov"], decision)[0]
    assert f"tenantid:{TENANT}" in option
    assert "use-tenantid" in option


@pytest.mark.parametrize("guid", [
    "FAE6D70F-954B-4811-92B6-0530D6F84C43",          # upper case is still a GUID
    "00000000-0000-0000-0000-000000000000",
])
def test_guid_forms_are_accepted(guid):
    option = clouds.CLOUDS["usgov"].freerdp_azure_option(guid)
    assert f"tenantid:{guid}" in option


# -- in-session card redirection -------------------------------------------

def test_reads_the_feeds_smartcard_request(tmp_path):
    from avd_feed_connect.rdp.resources import rdp_redirects_smartcards
    assert rdp_redirects_smartcards(write(tmp_path, GOV_RDP)) is True
    assert rdp_redirects_smartcards(write(tmp_path, COMMERCIAL_RDP, "c.rdp")) is False
    assert rdp_redirects_smartcards(str(tmp_path / "absent.rdp")) is False


def test_cli_redirects_the_card_when_the_feed_asks(tmp_path):
    argv = build_argv("sdl-freerdp", write(tmp_path, GOV_RDP), "user@example.mil",
                      cloud=clouds.CLOUDS["usgov"])
    assert "/smartcard" in argv
    # Never the logon variant: that is Kerberos to the host and needs /sec:nla.
    assert not any(a.startswith("/smartcard-logon") for a in argv)


def test_cli_leaves_the_card_alone_when_the_feed_does_not_ask(tmp_path):
    argv = build_argv("sdl-freerdp", write(tmp_path, COMMERCIAL_RDP, "c.rdp"),
                      "u@example.com", cloud=clouds.DEFAULT)
    assert "/smartcard" not in argv


@pytest.mark.parametrize("forced,expected", [(True, True), (False, False)])
def test_cli_smartcard_can_be_forced_either_way(tmp_path, forced, expected):
    argv = build_argv("sdl-freerdp", write(tmp_path, COMMERCIAL_RDP, "c.rdp"),
                      "u@example.com", cloud=clouds.DEFAULT, smartcard=forced)
    assert ("/smartcard" in argv) is expected
