"""CAC / PIV smartcard certificates for TLS client authentication.

Entra certificate-based authentication — what DoD tenants use for CAC sign-in —
asks for a TLS client certificate during the handshake. WebKitGTK surfaces that
as a ``CLIENT_CERTIFICATE_REQUESTED`` authentication request, which we answer
with a :class:`Gio.TlsCertificate` built from PKCS#11 URIs. The private key
never leaves the card: GnuTLS asks the card to sign, and the PIN is what
authorises that.

Discovery goes through p11-kit and ``p11tool`` rather than loading a PKCS#11
module directly. They are the registry and the tool every GnuTLS consumer
already honours, both ship in the GNOME runtime, and GIO exposes no PKCS#11
enumeration API — so this needs no new dependency and finds whatever middleware
the user has configured.

GTK-free, and :mod:`gi` is imported lazily, so probing and URI building are
unit-testable without a display or a card.
"""

import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass

# PIV slot 9A ("PIV Authentication") is the TLS client-auth certificate on a
# CAC. 9C signs, 9D encrypts, and 9E is the contactless card-authentication
# key — neither of the last two is a user sign-in credential, so they are
# ranked last rather than offered first.
_PREFERENCE = (
    "piv authentication",
    "authentication",
    "digital signature",
)
_DEPRIORITISED = ("card authentication", "key management")

# p11-kit's own trust module holds CA roots, never a user's card.
_TRUST_HINT = "trust"

# Enumerating public objects needs no PIN. Everything here runs with stdin
# closed and a timeout anyway, so a middleware that decides to prompt can
# neither hang the UI nor burn one of the card's few PIN attempts.
_TIMEOUT = 20
_PROBE_TTL = 5.0          # seconds; a card can be inserted or pulled at any time
_cache = {"at": 0.0, "certs": ()}


@dataclass(frozen=True)
class CardCertificate:
    """One certificate on a card, with the URIs needed to use it."""

    cert_uri: str
    key_uri: str
    label: str
    token: str

    @property
    def is_preferred(self):
        return _rank(self.label) == 0


def _rank(label):
    """Sort key for certificate choice; lower is better.

    Deprioritised names are checked FIRST: "Card Authentication" would
    otherwise match the generic "authentication" rule and outrank the real
    signing credentials.
    """
    lowered = (label or "").lower()
    if any(name in lowered for name in _DEPRIORITISED):
        return len(_PREFERENCE) + 1
    for index, name in enumerate(_PREFERENCE):
        if name in lowered:
            return index
    return len(_PREFERENCE)


def _run(argv):
    """Run a short-lived helper, returning stdout ('' on any failure)."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def _modules():
    """``(name, path)`` for every PKCS#11 module p11-kit knows, trust aside."""
    name, found = None, []
    for line in _run(["p11-kit", "list-modules"]).splitlines():
        stripped = line.strip()
        if stripped.startswith("module:"):
            name = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("path:") and name and _TRUST_HINT not in name.lower():
            path = stripped.split(":", 1)[1].strip()
            if path.startswith("/"):
                found.append((name, path))
    return tuple(found)


def token_uris():
    """PKCS#11 token URIs for every inserted card (p11-kit's trust module aside)."""
    name, found = None, []
    for line in _run(["p11-kit", "list-modules"]).splitlines():
        stripped = line.strip()
        if stripped.startswith("module:"):
            name = stripped.split(":", 1)[1].strip()
            continue
        match = re.match(r"uri:\s*(pkcs11:\S*token=\S*)", stripped)
        if match and name and _TRUST_HINT not in name.lower():
            found.append(match.group(1))
    return tuple(found)


def key_uri_for(cert_uri, pin=None):
    """The private-key URI matching ``cert_uri``.

    The key object's ``CKA_LABEL`` differs from the certificate's, so the two
    are matched on ``id=`` — which is what GnuTLS itself does. ``object=`` is
    dropped for that reason.

    A ``pin`` is embedded as ``pin-value``. That is what lets GnuTLS sign
    without a separate callback, needed because WebKit does not reliably emit
    ``CLIENT_CERTIFICATE_PIN_REQUESTED`` before the key is used. The result is
    held in memory only — never log or persist it.
    """
    kept = [part for part in cert_uri.split(";")
            if not part.startswith(("object=", "type="))]
    key_uri = ";".join(kept) + ";type=private"
    if pin:
        key_uri += "?pin-value=" + urllib.parse.quote(pin, safe="")
    return key_uri


def list_certificates(refresh=False):
    """Certificates across every inserted card, best candidate first.

    Cached briefly: this shells out, and both the settings UI and the sign-in
    path ask. Returns ``()`` on any failure — a missing or unreadable card is
    an expected, recoverable state, never an exception.
    """
    now = time.monotonic()
    if not refresh and now - _cache["at"] < _PROBE_TTL:
        return _cache["certs"]
    certs = []
    # Enumerate per MODULE rather than per token: filtering p11tool by a token
    # URI finds nothing when the module is reached through p11-kit's proxy
    # (as it is inside the Flatpak), while --provider works in both cases.
    for _name, path in _modules():
        for line in _run(["p11tool", "--provider", path,
                          "--list-all-certs", "--only-urls"]).splitlines():
            uri = line.strip()
            if not uri.startswith("pkcs11:") or "type=cert" not in uri:
                continue
            match = re.search(r"object=([^;]*)", uri)
            label = urllib.parse.unquote(match.group(1)) if match else ""
            token = uri.split(";id=")[0]
            certs.append(CardCertificate(cert_uri=uri, key_uri=key_uri_for(uri),
                                         label=label, token=token))
    certs.sort(key=lambda c: _rank(c.label))
    _cache.update(at=now, certs=tuple(certs))
    return _cache["certs"]


def available(refresh=False):
    """True when at least one usable card certificate is visible."""
    if os.environ.get("AVD_NO_SMARTCARD"):
        return False
    return bool(list_certificates(refresh=refresh))


def preferred(certs=None):
    """The certificate to use for sign-in, or None when there is no card."""
    certs = list_certificates() if certs is None else certs
    return certs[0] if certs else None


def load_certificate(card, pin=None):
    """A :class:`Gio.TlsCertificate` for ``card``, or None if it cannot be built.

    Construction does not need the PIN — GnuTLS defers it to signing time,
    which is where WebKit's PIN request fires — but embedding one here covers
    the case where that request never comes.
    """
    from gi.repository import Gio, GLib

    if card is None:
        return None
    try:
        return Gio.TlsCertificate.new_from_pkcs11_uris(
            card.cert_uri, key_uri_for(card.cert_uri, pin))
    except GLib.Error:
        return None


def describe(certificate):
    """Short human description of a loaded certificate, for the UI and logs."""
    if certificate is None:
        return ""
    try:
        subject = certificate.get_subject_name() or ""
    except (AttributeError, TypeError):
        return ""
    common = re.search(r"CN=([^,]+)", subject)
    return common.group(1) if common else subject


def sandbox_paths():
    """Paths WebKit's network process needs in order to use the card.

    WebKit terminates TLS in a separate, bwrap-sandboxed network process. A
    PKCS#11-backed certificate cannot be handed over as bytes — the private key
    stays on the card — so that process has to reach the reader and the module
    itself. Without these it sees no certificate and the server reports that
    none was offered (Entra: AADSTS500192).

    Returns ``(path, read_only)`` pairs; missing paths are skipped by the
    caller, so this is safe to call on any system.
    """
    socket = os.environ.get("PCSCLITE_CSOCK_NAME") or "/run/pcscd/pcscd.comm"
    paths = [
        # The pcscd socket needs WRITE access; it is a two-way conversation.
        (os.path.dirname(socket) or "/run/pcscd", False),
    ]
    # Wherever p11-kit says the modules actually live, plus its config.
    module = ""
    for line in _run(["p11-kit", "list-modules"]).splitlines():
        stripped = line.strip()
        if stripped.startswith("path:"):
            module = stripped.split(":", 1)[1].strip()
            if module.startswith("/"):
                paths.append((os.path.dirname(module), True))
                # Distros ship the registered module as a symlink into a
                # sibling directory (…/pkcs11/opensc-pkcs11.so ->
                # ../opensc-pkcs11.so). Binding only the link's directory
                # leaves it dangling, so bind the target's directory as well.
                real = os.path.realpath(module)
                if real != module:
                    paths.append((os.path.dirname(real), True))
    paths += [("/usr/lib/pkcs11", True),
              ("/etc/pkcs11", True),
              (os.path.dirname(os.path.dirname(module_config_path())), True)]
    seen, unique = set(), []
    for path, read_only in paths:
        if path and path not in seen:
            seen.add(path)
            unique.append((path, read_only))
    return unique


def grant_sandbox_access(web_context):
    """Let WebKit's network process reach the card. Call BEFORE any WebView.

    ``add_path_to_sandbox`` only affects processes started afterwards, so this
    has to run while no web process exists yet.
    """
    granted = []
    for path, read_only in sandbox_paths():
        if not os.path.exists(path):
            continue
        try:
            web_context.add_path_to_sandbox(path, read_only)
            granted.append(path)
        except Exception:
            continue
    return granted


# p11-kit reads module configs from its system dir and, only when the system
# config allows it, from the user dir. The GNOME runtime ships
# /etc/pkcs11/pkcs11.conf containing "user-config: none", so inside the Flatpak
# the user dir is ignored outright — but /etc there is a per-instance tmpfs and
# IS writable, so the system dir is the one that works. Unpackaged, /etc is the
# real one and not writable, and the host's module is already registered
# anyway, so the user dir is the right fallback.
_SYSTEM_MODULE_DIR = "/etc/pkcs11/modules"


def module_config_paths():
    """Candidate locations for the module config, best first."""
    user_base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return (os.path.join(_SYSTEM_MODULE_DIR, "opensc.module"),
            os.path.join(user_base, "pkcs11", "modules", "opensc.module"))


def module_config_path():
    """The location that would be used first (kept for callers and tests)."""
    return module_config_paths()[0]


def ensure_module_registered(module_path="/app/lib/opensc-pkcs11.so"):
    """Register ``module_path`` with p11-kit if it exists and is not registered.

    Idempotent, and a no-op when the module is absent (unpackaged runs use the
    host's already-registered module). Returns True when a registration is in
    place.
    """
    if not os.path.exists(module_path):
        return False
    want = f"module: {module_path}\ncritical: no\n"
    for path in module_config_paths():
        try:
            with open(path) as source:
                if source.read() == want:
                    return True
        except OSError:
            pass
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as destination:
                destination.write(want)
            return True
        except OSError:
            continue
    return False
