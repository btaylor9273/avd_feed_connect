"""The Azure cloud (sovereign environment) table.

Azure Virtual Desktop exists in more than one cloud, and every cloud has its
own Entra authority, AVD resource/scope and feed-discovery endpoint. Microsoft's
Windows App picks these from the workspace URL; we keep them in one table and
attach the chosen :class:`Cloud` to the signed-in account, because a single
install can hold a commercial account and a US Government account at once.

Deliberately dependency-free (no :mod:`config` import) so the table stays a
plain data lookup that both the GUI and the CLI can use, and so the URL-trust
predicates can live next to the host lists they check against.
"""

import os
import re
import urllib.parse
from dataclasses import dataclass

# A tenant id is a GUID. It arrives in the feed's .rdp file, which is
# attacker-influenced data, and is interpolated into a FreeRDP command-line
# option whose sub-options are comma-separated — so anything but a GUID is
# refused rather than passed through. See freerdp_azure_option.
_GUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                   r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

# Hosts a bearer token may be sent to in ANY cloud. Microsoft-operated and
# shared: the commercial feed/CDN hosts still serve some assets for sovereign
# tenants, so they are trusted everywhere rather than per cloud.
_COMMON_TRUSTED = (".microsoft.com", ".microsoftonline.com")

# The ONLY redirect URI registered for the public client, in every cloud. Both
# this app's own sign-in and FreeRDP's connect-time sign-in must use exactly
# this; anything else — a loopback port, or the sovereign host/tenant variant
# FreeRDP derives by default — is refused with AADSTS50011.
NATIVECLIENT_REDIRECT = "https://login.microsoftonline.com/common/oauth2/nativeclient"


@dataclass(frozen=True)
class Cloud:
    """One Azure environment's endpoints.

    ``gateway_markers`` are host suffixes that identify this cloud from a feed
    resource or an ``.rdp`` gateway host, which is how an account whose cloud
    was never recorded gets classified (see :func:`detect_from_host`).
    """

    key: str
    label: str
    login_host: str
    avd_resource: str
    discovery: str
    trusted_suffixes: tuple
    gateway_markers: tuple = ()
    # Host for FreeRDP's /azure:ad: override. None omits ad: entirely, which
    # leaves FreeRDP's authorize URL on the commercial host while still fixing
    # the scope — the fallback if ad: also moves FreeRDP's redirect_uri to the
    # sovereign host and Entra answers AADSTS50011.
    freerdp_ad_host: str = None

    # -- OAuth --------------------------------------------------------------

    def login(self, tenant):
        """Base URL of the v2.0 authorize/token endpoints for ``tenant``."""
        return f"https://{self.login_host}/{tenant}/oauth2/v2.0"

    @property
    def scope(self):
        """Scope we request for feed access (plus refresh/identity scopes)."""
        return f"{self.avd_resource}/.default offline_access openid profile"

    @property
    def freerdp_scope(self):
        """Same scope in the order FreeRDP's own ``avd-scope`` expects."""
        return f"{self.avd_resource}/.default openid profile offline_access"

    # -- URL trust ----------------------------------------------------------

    @property
    def login_hosts(self):
        """Every Entra host this cloud may legitimately redirect sign-in to.

        Certificate-based auth (CAC) does not happen on the ordinary login
        host: Entra bounces to a dedicated ``certauth.`` host for the
        client-certificate handshake. Allowing only ``login_host`` therefore
        breaks CAC sign-in at the point the card is actually requested.
        """
        return (self.login_host, "certauth." + self.login_host)

    def is_login_url(self, url):
        """True only for an https URL on one of this cloud's Entra hosts.

        FreeRDP's "Browse to:" URL is loaded in a WebView that shares our SSO
        cookies, so nothing else may ever be loaded there. Exact host equality
        against a short allowlist — no suffix matching.
        """
        try:
            p = urllib.parse.urlparse(url)
        except ValueError:
            return False
        return p.scheme == "https" and (p.hostname or "").lower() in self.login_hosts

    def is_trusted_url(self, url):
        """True only for an https URL on a Microsoft host for this cloud.

        Dot-boundary match, so ``evilmicrosoft.com`` and
        ``microsoft.com.evil.io`` are rejected. Feed XML supplies the feed,
        .rdp and icon URLs, so they are checked before the Authorization
        header is attached.
        """
        try:
            p = urllib.parse.urlparse(url)
            host = (p.hostname or "").lower()
        except ValueError:
            return False
        return p.scheme == "https" and any(
            host == s[1:] or host.endswith(s) for s in self.trusted_suffixes)

    # -- FreeRDP ------------------------------------------------------------

    def freerdp_azure_option(self, tenant_id=None):
        """FreeRDP's ``/azure:`` override for this cloud, or None if not needed.

        FreeRDP defaults to the commercial authority and the commercial
        ``wvd.microsoft.com`` scope no matter what gateway the .rdp names, and
        has no sovereign auto-detection in any release through 3.33.0
        (FreeRDP#13457; PR#13329 would add it). Overriding ``ad:`` also covers
        the second, RDSTLS token request, so one option is enough.

        ``avd-access`` is always set, because FreeRDP derives its redirect from
        the same (host, tenant) pair as the authority — so overriding ``ad:``
        for a sovereign cloud also moves the redirect to
        ``https://login.microsoftonline.us/<tenant>/oauth2/nativeclient``,
        which is NOT registered and fails with AADSTS50011. Forcing it back to
        the registered commercial nativeclient is what makes the connect-time
        sign-in work.
        """
        if self.key == DEFAULT_KEY:
            return None
        parts = []
        if self.freerdp_ad_host:
            parts.append(f"ad:{self.freerdp_ad_host}")
        # Only a well-formed GUID may be interpolated. A comma in this value
        # would otherwise start a new FreeRDP sub-option, letting a hostile
        # .rdp add e.g. auth-helper: (a binary FreeRDP runs) or avd-access:
        # (where the authorization code is sent). Dropping it is safe: FreeRDP
        # then uses "common" against the right authority.
        if tenant_id and _GUID.match(str(tenant_id)):
            # use-tenantid makes FreeRDP send the tenant instead of "common",
            # which sovereign tenants require.
            parts += [f"tenantid:{tenant_id}", "use-tenantid"]
        parts.append("avd-scope:" + urllib.parse.quote(self.freerdp_scope, safe=""))
        # FreeRDP runs these values through printf, so every literal percent
        # has to be doubled or "%3A" is read as a format specifier. Its own
        # built-in default is written the same way.
        encoded = urllib.parse.quote(NATIVECLIENT_REDIRECT, safe="").replace("%", "%%")
        parts.append("avd-access:" + encoded)
        return "/azure:" + ",".join(parts)


DEFAULT_KEY = "commercial"

CLOUDS = {
    "commercial": Cloud(
        key="commercial",
        label="Azure (commercial)",
        login_host="login.microsoftonline.com",
        avd_resource="https://www.wvd.microsoft.com",
        discovery="https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery",
        trusted_suffixes=_COMMON_TRUSTED,
    ),
    "usgov": Cloud(
        key="usgov",
        label="Azure US Government (GCC High / DoD)",
        login_host="login.microsoftonline.us",
        avd_resource="https://www.wvd.azure.us",
        discovery="https://rdweb.wvd.azure.us/api/arm/feeddiscovery",
        # Only hosts this client actually sends the bearer token to, and only
        # Microsoft-operated ones. Deliberately NOT ".azure.us" or
        # ".usgovcloudapi.net": customers choose the leftmost labels under
        # those (e.g. <account>.blob.core.usgovcloudapi.net) and get valid
        # Microsoft TLS certs, so a feed-supplied URL there would hand the
        # access token to an unrelated third party. Every gov host observed in
        # a real feed — rdweb, rdweb-g-usg-r1, rdgateway-r1 — is under
        # .wvd.azure.us.
        trusted_suffixes=_COMMON_TRUSTED + (
            ".microsoftonline.us", ".wvd.azure.us"),
        gateway_markers=(".wvd.azure.us", ".azure.us", ".usgovcloudapi.net"),
        freerdp_ad_host="login.microsoftonline.us",
    ),
}

DEFAULT = CLOUDS[DEFAULT_KEY]

# Order used for the UI chooser; the default cloud comes first.
ORDER = (DEFAULT_KEY, "usgov")
LABELS = tuple(CLOUDS[k].label for k in ORDER)


def resolve(key=None):
    """The cloud for ``key``, falling back to ``$AVD_CLOUD`` then the default.

    Unknown keys fall back rather than raise: a settings file or env var from a
    newer version must not stop the app from starting.
    """
    for candidate in (key, os.environ.get("AVD_CLOUD")):
        if candidate:
            cloud = CLOUDS.get(str(candidate).strip().lower())
            if cloud is not None:
                return cloud
    return DEFAULT


def detect_from_host(host):
    """The non-default cloud whose markers match ``host``, else None.

    Used to classify an account recorded before clouds existed, from a cached
    feed URL or an ``.rdp`` gateway host.
    """
    host = (host or "").lower().strip()
    if not host:
        return None
    for key in ORDER:
        cloud = CLOUDS[key]
        if cloud.key == DEFAULT_KEY:
            continue
        if any(host == m[1:] or host.endswith(m) for m in cloud.gateway_markers):
            return cloud
    return None


def detect_from_url(url):
    """:func:`detect_from_host` on the hostname of ``url``."""
    try:
        return detect_from_host(urllib.parse.urlparse(url).hostname)
    except ValueError:
        return None
