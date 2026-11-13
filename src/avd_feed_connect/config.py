"""Static configuration: the OAuth/feed endpoints, on-disk locations, and the
path to the bundled sdl-freerdp.

These are process-wide constants (mostly derived from the environment once, at
import time). Anything that changes per signed-in session — the user's UPN, the
last refresh error — lives on :class:`~avd_feed_connect.auth.oauth.OAuthClient`
instead, never here.

The endpoint constants below describe ONE cloud: the process-wide default from
:func:`avd_feed_connect.clouds.resolve` (commercial unless ``$AVD_CLOUD`` says
otherwise). They remain for the CLI and for callers with no account in hand,
but anything that runs per account should take its endpoints from that
account's :class:`~avd_feed_connect.clouds.Cloud` instead — a single install
can hold a commercial account and a US Government account at the same time.
"""

import os
import shutil

from . import clouds

# Multi-tenant by default: "organizations" lets any work/school account sign in
# and the feed returns every workspace that account is entitled to. Override
# with AVD_TENANT to pin a single tenant. The signed-in user's UPN is normally
# learned from the token (OAuthClient.set_upn_from_token); AVD_UPN can pre-fill
# the login hint (see OAuthClient).
TENANT = os.environ.get("AVD_TENANT", "organizations")
CLIENT_ID = "a85cf173-4192-42f8-81fa-777a763e6e2c"  # Microsoft Remote Desktop (public)

# Process-wide default cloud; per-account clouds come from the profile registry.
CLOUD = clouds.resolve()
SCOPE = CLOUD.scope
DISCOVERY = CLOUD.discovery
LOGIN = CLOUD.login(TENANT)
# Registered redirect for the public MS Remote Desktop client; the browser
# lands here (a blank page) with ?code=… after an interactive sign-in.
#
# This stays on login.microsoftonline.com in EVERY cloud. It is the reply URL
# registered for the public client, and sovereign clouds accept it as-is;
# "correcting" it to a .us host yields AADSTS50011.
REDIRECT = clouds.NATIVECLIENT_REDIRECT
# The feed service rejects unknown clients ("INCOMPATIBLE_CLIENT_VERSION /
# Client did not send any User Agent approved header"); this is exactly the
# X-MS-User-Agent the web client sends (clientType/clientVersion sdkType/sdk).
MS_USER_AGENT = "com.microsoft.rdc.html/2.0.79.2 rdhtml-sdk/2.0.4"

ACCEPT_DISCOVERY = "application/x-msts-radc-discovery+xml,text/xml"
ACCEPT_FEED = "application/x-msts-radc+xml;radc_schema_version=2.0,text/xml"

HOME = os.path.expanduser("~")

# Data dir: use XDG_DATA_HOME (set to the app's private dir inside Flatpak) so
# the token cache and generated .rdp files land in a sane, writable place both
# packaged and unpackaged.
_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
OUT = os.path.join(_DATA, "avd-feed-connect", "feed")
CACHE = os.path.join(_DATA, "avd-feed-connect", "token-cache.json")


def find_sdl_freerdp():
    """Locate the SDL3 FreeRDP client. In the Flatpak it is on PATH at
    /app/bin/sdl-freerdp; unpackaged, fall back to the local -cam build."""
    return (os.environ.get("AVD_SDL_FREERDP")
            or shutil.which("sdl-freerdp")
            or os.path.join(HOME, "opt", "freerdp-sdl3-cam", "bin", "sdl-freerdp"))


SDL = find_sdl_freerdp()
# Only needed unpackaged (Flatpak resolves libs via rpath); empty = don't touch.
SDL_LIBS = os.environ.get("AVD_SDL_LIBS", os.path.join(HOME, "opt", "sdl3", "lib"))

AAD_LOGIN_HOST = CLOUD.login_host

# Hosts a bearer token may be sent to. Feed XML supplies the feed, .rdp and icon
# URLs, so they are checked before the Authorization header is attached.
TRUSTED_SUFFIXES = CLOUD.trusted_suffixes


def is_aad_login_url(url, cloud=None):
    """True only for an https URL on the Entra login host. FreeRDP's "Browse to:"
    URL is loaded in a WebView that shares our SSO cookies, so nothing else
    may be loaded there."""
    return (cloud or CLOUD).is_login_url(url)


def is_trusted_url(url, cloud=None):
    """True only for an https URL on a Microsoft host (dot-boundary match, so
    ``evilmicrosoft.com`` and ``microsoft.com.evil.io`` are rejected)."""
    return (cloud or CLOUD).is_trusted_url(url)
