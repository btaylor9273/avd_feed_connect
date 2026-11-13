"""Smartcard (PKCS#11) discovery and URI handling.

Never touches a real reader: the external tools are stubbed, so these run in CI
with no card and can never trigger a PIN prompt (which would burn one of the
card's few attempts).
"""
import pytest

from avd_feed_connect.auth import smartcard

TOKEN = ("pkcs11:model=PKCS%2315%20emulated;manufacturer=piv_II;"
         "serial=0123456789abcdef;token=EXAMPLE.USER.1234567890")

# Real `p11tool --list-all-certs --only-urls` output for a DoD CAC (name changed).
P11TOOL_CERTS = "\n".join(
    f"{TOKEN};id=%0{n};object=Certificate%20for%20{label};type=cert"
    for n, label in ((1, "PIV%20Authentication"), (2, "Digital%20Signature"),
                     (3, "Key%20Management"), (4, "Card%20Authentication")))

MODULE_PATH = "/usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so"

P11_KIT_MODULES = f"""module: p11-kit-trust
    path: /usr/lib/x86_64-linux-gnu/pkcs11/p11-kit-trust.so
    uri: pkcs11:library-description=PKCS%2311%20Kit%20Trust%20Module
    token: System Trust
        uri: pkcs11:model=p11-kit-trust;manufacturer=PKCS%2311%20Kit;serial=1;token=System%20Trust
module: opensc-pkcs11
    path: {MODULE_PATH}
    uri: pkcs11:library-description=OpenSC%20smartcard%20framework
    token: EXAMPLE.USER.1234567890
        uri: {TOKEN}
"""


@pytest.fixture(autouse=True)
def no_cache():
    smartcard._cache.update(at=0.0, certs=())
    yield
    smartcard._cache.update(at=0.0, certs=())


def fake_tools(monkeypatch, modules=P11_KIT_MODULES, certs=P11TOOL_CERTS):
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "p11-kit":
            return modules
        if argv[0] == "p11tool":
            return certs
        return ""
    monkeypatch.setattr(smartcard, "_run", run)
    return calls


# -- token discovery --------------------------------------------------------

def test_token_discovery_skips_the_trust_module(monkeypatch):
    fake_tools(monkeypatch)
    assert smartcard.token_uris() == (TOKEN,)


def test_module_discovery_skips_the_trust_module(monkeypatch):
    """Enumeration keys off the module path, not a token filter: filtering
    p11tool by a token URI finds nothing when the module is reached through
    p11-kit's proxy, as it is inside the Flatpak."""
    fake_tools(monkeypatch)
    assert smartcard._modules() == (("opensc-pkcs11", MODULE_PATH),)


def test_no_tools_means_no_tokens(monkeypatch):
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    assert smartcard.token_uris() == ()
    assert smartcard._modules() == ()
    assert smartcard.available() is False


# -- certificate enumeration and choice -------------------------------------

def test_lists_every_certificate_on_the_card(monkeypatch):
    fake_tools(monkeypatch)
    certs = smartcard.list_certificates()
    assert len(certs) == 4
    assert all(c.token == TOKEN for c in certs)


def test_piv_authentication_is_preferred(monkeypatch):
    """The PIV Authentication cert is the TLS client-auth credential."""
    fake_tools(monkeypatch)
    assert smartcard.preferred().label == "Certificate for PIV Authentication"


def test_card_authentication_and_key_management_rank_last(monkeypatch):
    """Card Authentication is the contactless CAK, not a sign-in credential —
    and it must not win on merely containing the word "Authentication"."""
    fake_tools(monkeypatch)
    labels = [c.label for c in smartcard.list_certificates()]
    assert labels[0] == "Certificate for PIV Authentication"
    for trailing in ("Certificate for Key Management",
                     "Certificate for Card Authentication"):
        assert labels.index(trailing) > labels.index("Certificate for Digital Signature")


def test_labels_are_percent_decoded(monkeypatch):
    fake_tools(monkeypatch)
    assert all("%20" not in c.label for c in smartcard.list_certificates())


def test_non_certificate_lines_are_ignored(monkeypatch):
    fake_tools(monkeypatch, certs="warning: something\n\nnot a uri\n" + P11TOOL_CERTS)
    assert len(smartcard.list_certificates()) == 4


def test_no_card_is_not_an_error(monkeypatch):
    fake_tools(monkeypatch, certs="")
    assert smartcard.list_certificates() == ()
    assert smartcard.preferred() is None
    assert smartcard.available() is False


def test_env_override_disables_smartcards(monkeypatch):
    fake_tools(monkeypatch)
    monkeypatch.setenv("AVD_NO_SMARTCARD", "1")
    assert smartcard.available() is False


def test_results_are_cached_then_refreshable(monkeypatch):
    calls = []

    def run(argv):
        calls.append(argv[0])
        return P11_KIT_MODULES if argv[0] == "p11-kit" else P11TOOL_CERTS
    monkeypatch.setattr(smartcard, "_run", run)
    smartcard.list_certificates()
    first = len(calls)
    smartcard.list_certificates()
    assert len(calls) == first, "a second call within the TTL must be cached"
    smartcard.list_certificates(refresh=True)
    assert len(calls) > first


# -- key URI derivation -----------------------------------------------------

def test_key_uri_matches_on_id_and_drops_the_object():
    """The private key's label differs from the certificate's, so the two are
    matched on id= — which is what GnuTLS itself does."""
    cert_uri = f"{TOKEN};id=%01;object=Certificate%20for%20PIV%20Authentication;type=cert"
    key_uri = smartcard.key_uri_for(cert_uri)
    assert key_uri.endswith(";type=private")
    assert "object=" not in key_uri
    assert "type=cert" not in key_uri
    assert "id=%01" in key_uri
    for kept in ("model=", "manufacturer=", "serial=", "token="):
        assert kept in key_uri


def test_key_uri_can_carry_a_pin():
    key_uri = smartcard.key_uri_for(f"{TOKEN};id=%01;type=cert", pin="12345678")
    assert key_uri.endswith("?pin-value=12345678")


def test_pin_is_percent_encoded_in_the_uri():
    key_uri = smartcard.key_uri_for(f"{TOKEN};id=%01;type=cert", pin="a b&c")
    assert "pin-value=a%20b%26c" in key_uri


def test_no_pin_means_no_pin_value():
    assert "pin-value" not in smartcard.key_uri_for(f"{TOKEN};id=%01;type=cert")


def test_load_certificate_tolerates_no_card():
    assert smartcard.load_certificate(None) is None


def test_describe_handles_nothing():
    assert smartcard.describe(None) == ""


# -- p11-kit module registration -------------------------------------------

def test_module_config_prefers_the_system_dir(monkeypatch, tmp_path):
    """The GNOME runtime ships pkcs11.conf with "user-config: none", so inside
    the Flatpak the user dir is ignored and only /etc works — and /etc there is
    a per-instance tmpfs, so it is writable."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    paths = smartcard.module_config_paths()
    assert paths[0] == "/etc/pkcs11/modules/opensc.module"
    assert paths[1] == str(tmp_path / "pkcs11" / "modules" / "opensc.module")
    assert smartcard.module_config_path() == paths[0]


def test_registration_is_a_no_op_without_the_module(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(smartcard, "_SYSTEM_MODULE_DIR", str(tmp_path / "etc"))
    assert smartcard.ensure_module_registered(str(tmp_path / "absent.so")) is False
    assert not (tmp_path / "pkcs11").exists()


def test_registration_writes_once_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(smartcard, "_SYSTEM_MODULE_DIR", str(tmp_path / "etc"))
    module = tmp_path / "opensc-pkcs11.so"
    module.write_text("")
    assert smartcard.ensure_module_registered(str(module)) is True
    written = tmp_path / "etc" / "opensc.module"
    assert f"module: {module}" in written.read_text()
    before = written.stat().st_mtime_ns
    assert smartcard.ensure_module_registered(str(module)) is True
    assert written.stat().st_mtime_ns == before, "must not rewrite an identical config"


# -- WebKit sandbox access --------------------------------------------------

P11_KIT_PATHS = """module: p11-kit-trust
    path: /usr/lib/x86_64-linux-gnu/pkcs11/p11-kit-trust.so
module: opensc-pkcs11
    path: /usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so
"""


def test_sandbox_includes_the_pcscd_socket_directory_writable(monkeypatch):
    """The pcscd socket is a two-way conversation, so read-only will not do."""
    monkeypatch.setattr(smartcard, "_run", lambda argv: P11_KIT_PATHS)
    monkeypatch.setenv("PCSCLITE_CSOCK_NAME", "/run/pcscd/pcscd.comm")
    paths = dict(smartcard.sandbox_paths())
    assert paths["/run/pcscd"] is False      # False == not read-only


def test_sandbox_honours_a_relocated_socket(monkeypatch):
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    monkeypatch.setenv("PCSCLITE_CSOCK_NAME", "/custom/run/pcscd.comm")
    assert "/custom/run" in dict(smartcard.sandbox_paths())


def test_sandbox_includes_module_directories_read_only(monkeypatch):
    monkeypatch.setattr(smartcard, "_run", lambda argv: P11_KIT_PATHS)
    paths = dict(smartcard.sandbox_paths())
    assert paths["/usr/lib/x86_64-linux-gnu/pkcs11"] is True


def test_sandbox_follows_a_symlinked_module(monkeypatch, tmp_path):
    """Distros register the module as a symlink into a sibling directory;
    binding only the link's directory would leave it dangling."""
    real_dir = tmp_path / "lib"
    link_dir = tmp_path / "lib" / "pkcs11"
    real_dir.mkdir(parents=True)
    link_dir.mkdir()
    real = real_dir / "opensc-pkcs11.so"
    real.write_text("")
    link = link_dir / "opensc-pkcs11.so"
    link.symlink_to("../opensc-pkcs11.so")
    monkeypatch.setattr(smartcard, "_run", lambda argv: f"module: opensc\n    path: {link}\n")
    paths = dict(smartcard.sandbox_paths())
    assert str(link_dir) in paths
    assert str(real_dir) in paths, "the symlink target's directory must be bound too"


def test_sandbox_paths_are_deduplicated(monkeypatch):
    monkeypatch.setattr(smartcard, "_run", lambda argv: P11_KIT_PATHS)
    paths = [path for path, _ in smartcard.sandbox_paths()]
    assert len(paths) == len(set(paths))


def test_grant_skips_paths_that_do_not_exist(monkeypatch):
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    monkeypatch.setenv("PCSCLITE_CSOCK_NAME", "/definitely/not/here/pcscd.comm")
    added = []

    class Context:
        def add_path_to_sandbox(self, path, read_only):
            added.append((path, read_only))

    assert smartcard.grant_sandbox_access(Context()) == []
    assert added == []


def test_grant_survives_a_failing_context(monkeypatch, tmp_path):
    """A WebKit version without the call must not break sign-in."""
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    monkeypatch.setenv("PCSCLITE_CSOCK_NAME", str(tmp_path / "pcscd.comm"))

    class Context:
        def add_path_to_sandbox(self, path, read_only):
            raise RuntimeError("unsupported")

    assert smartcard.grant_sandbox_access(Context()) == []


def test_grant_reports_what_it_added(monkeypatch, tmp_path):
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    monkeypatch.setenv("PCSCLITE_CSOCK_NAME", str(tmp_path / "pcscd.comm"))

    class Context:
        def __init__(self):
            self.added = []

        def add_path_to_sandbox(self, path, read_only):
            self.added.append((path, read_only))

    context = Context()
    granted = smartcard.grant_sandbox_access(context)
    assert str(tmp_path) in granted
    assert (str(tmp_path), False) in context.added


def test_p11tool_is_called_with_the_module_provider(monkeypatch):
    calls = fake_tools(monkeypatch)
    smartcard.list_certificates(refresh=True)
    p11tool = [c for c in calls if c[0] == "p11tool"]
    assert p11tool, "p11tool was never invoked"
    assert "--provider" in p11tool[0]
    assert MODULE_PATH in p11tool[0]


# -- the default must work on a fresh profile -------------------------------

def test_cac_defaults_on_when_a_card_is_present(monkeypatch):
    """A fresh profile has no _cac setting. Defaulting it off presents a
    sign-in page that a smart-card-only tenant cannot complete."""
    import avd_feed_gui as gui
    fake_tools(monkeypatch)

    app = gui.AvdApp.__new__(gui.AvdApp)      # no GTK init needed
    app._settings = {}
    assert app._cac_enabled() is True
    app._settings = {"_cac": "off"}
    assert app._cac_enabled() is False
    app._settings = {"_cac": "on"}
    assert app._cac_enabled() is True


def test_cac_defaults_off_without_a_card(monkeypatch):
    import avd_feed_gui as gui
    monkeypatch.setattr(smartcard, "_run", lambda argv: "")
    app = gui.AvdApp.__new__(gui.AvdApp)
    app._settings = {}
    assert app._cac_enabled() is False
