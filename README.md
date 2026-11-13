<p align="center">
  <img src="assets/logo.png" width="120" alt="AVD Feed + Connect logo">
</p>

<h1 align="center">AVD Feed + Connect (Linux)</h1>

<p align="center">
A native Linux client for <b>Azure Virtual Desktop</b> and <b>Windows 365</b> —
sign in, see the desktops and remote apps you're entitled to (just like
Microsoft's Windows App), and connect over RDP.
</p>

> Unofficial. Not affiliated with or endorsed by Microsoft.

## Screenshots

<p align="center">
  <img src="screenshots/workspaces.png" width="49%" alt="Workspace grid — light">
  <img src="screenshots/workspaces-dark.png" width="49%" alt="Workspace grid — dark">
  <br>
  <em>Your Azure Virtual Desktop workspaces after sign-in — light and dark
  (switchable in-app, or follow the system). Example data shown.</em>
</p>

## Origin and changes

This is a modified build of
[shakeelosmani/avd_feed_connect](https://github.com/shakeelosmani/avd_feed_connect)
(MIT, see `LICENSE`), published under its own application ID
(`io.github.btaylor9273.avd_feed_connect`) so it does not shadow the original.

Changes in this fork:

* **Azure US Government (GCC High / DoD) support** — a cloud table selecting the
  Entra authority, AVD scope and feed endpoint per account, with FreeRDP's
  sovereign-cloud override passed at connect time.
* **Smart card (CAC/PIV) sign-in** — the card answers Entra's
  certificate-based authentication inside the app; OpenSC and the pcsc-lite
  client library are bundled.
* **The Teams call-optimization add-in is NOT bundled.** It builds with network
  access and unpinned Rust dependencies, which is incompatible with an
  air-gapped, auditable build. Its settings remain visible but inert.

For review: `docs/DEPENDENCIES.md`, `docs/PERMISSIONS.md`, `docs/OFFLINE-BUILD.md`.

## Why

Linux has FreeRDP, but no client that does **feed discovery** — the step where
you sign in and the client lists your AVD workspaces instead of you
hand-authoring an `.rdp`/`.rdpw` file per host pool. This app adds that, and
drives a bundled, hardened FreeRDP for the actual connection.

## How it works

1. **Interactive Entra ID sign-in** (auth-code + PKCE, in an embedded WebKit
   view). This is the same flow the official clients use, so Conditional Access
   policies that block the device-code flow are honored.
2. **Feed discovery** against `rdweb.wvd.microsoft.com/api/arm/feeddiscovery`
   — or `rdweb.wvd.azure.us` for an Azure US Government account (sending the
   approved `X-MS-User-Agent` the service requires).
3. **Workspace grid** with the real per-resource icons; double-click to connect.
4. **Connect** by downloading the resource's `.rdp` from the feed and launching
   the bundled `sdl-freerdp` with `/gateway:type:arm /sec:aad`. The
   connection's Entra token is obtained **silently** in the background (see
   below) — no second sign-in window unless your org requires MFA at connect.

Token refresh is the standard OAuth2 `refresh_token` grant (offline_access);
the app renews the access token silently before it expires. The refresh token
is stored in your **login keyring** (via libsecret), encrypted at rest — not in
a plaintext file. The embedded sign-in keeps your Microsoft SSO session between
launches, so re-authenticating (for example when a Conditional Access policy
expires your token) is usually a single click rather than a full password+MFA.

## Multiple work accounts

Open the avatar/email menu and choose **Add account** to sign in to another
company. The same menu lists saved accounts; choosing one displays its
workspaces. Each entry shows a custom account name, or the email domain, above the account email.
Use the pencil button to set a local display name; leaving it empty restores
the email domain. Existing remote desktop connections stay open when you switch.
The tray groups workspaces by account and supports connecting, focusing, and
disconnecting sessions independently of the account shown in the window.

Each account has separate credentials, Microsoft sign-in cookies, workspace
and icon caches, connection files, and resource preferences. **Sign out**
removes only the selected account. Established RDP sessions remain open until
you disconnect them. Saved sessions from earlier versions are migrated on
first launch; global preferences are retained.

The separate **Settings** (gear) menu contains **Appearance**, **System tray**,
and **Default settings…**. These apply across accounts; right-clicking a
workspace changes that account's resource preferences. The CLI uses the
selected saved account; `logout` clears that account's credentials and profile.
A combined view of all accounts is deferred to a later release.

## Azure US Government (GCC High / DoD) and CAC sign-in

Azure Virtual Desktop exists in more than one cloud, and each has its own Entra
authority, AVD scope and feed endpoint. Pick the cloud when you add the
account — **Add account** asks, because the authority has to be known *before*
sign-in and so cannot be discovered from the feed:

| | Commercial | US Government |
|---|---|---|
| Sign-in | `login.microsoftonline.com` | `login.microsoftonline.us` |
| Feed | `rdweb.wvd.microsoft.com` | `rdweb.wvd.azure.us` |
| AVD scope | `https://www.wvd.microsoft.com/.default` | `https://www.wvd.azure.us/.default` |

Accounts added before this release are classified automatically from the
gateway host in their cached feed, so an existing `.mil` account keeps working.
`AVD_CLOUD=usgov` pins the cloud for the CLI and for new accounts.

For the connection itself, FreeRDP defaults to the commercial authority and
scope no matter which gateway the `.rdp` names, and has no sovereign-cloud
detection in any release so far
([FreeRDP#13457](https://github.com/FreeRDP/FreeRDP/issues/13457)). The app
therefore passes FreeRDP's own override explicitly:

```
/azure:ad:login.microsoftonline.us,tenantid:<aadtenantid>,use-tenantid,avd-scope:<gov scope>
```

### Signing in with a smart card (CAC / PIV)

Many organizations require a smart card and accept nothing else. The app reads
the card directly: when Entra asks for a client certificate, it offers the
card's **PIV Authentication** certificate (PIV slot 9A) and prompts for your
PIN in the app window. This is on by default whenever a card is readable.

**The private key never leaves the card.** The app reads only the certificate;
GnuTLS has the card itself sign the TLS handshake, through the bundled OpenSC
PKCS#11 module and the host's `pcscd`. Your PIN is held in memory for the life
of the process — never written to settings, the keyring, or any log — and is
cleared on sign-out. A wrong PIN is never retried automatically, because a CAC
locks after a few failures.

If that does not work on your card, **Settings → Sign-in → Sign in using my
browser instead** hands sign-in to your normal browser, which has its own
PKCS#11 setup. The only redirect URI registered for this public client is a
Microsoft page the app cannot read, so that path asks you to paste one address
back; it is the fallback, not the default.

### Using the card inside the session

**Settings → Sign-in → Use my smart card in the session** forwards your reader
to the remote desktop over FreeRDP's PC/SC channel, so CAC-signed mail and
`.mil` sites work there. It follows the feed's own `redirectsmartcards`
request unless you set it explicitly, and is independent of signing *in* with
the card.

This is plain `/smartcard` redirection, never `/smartcard-logon` — that one is
Kerberos/PKINIT to the session host and requires `/sec:nla`, which conflicts
with the Entra path AVD uses.

Two things to know. The app also holds the card during sign-in, so both uses
share one reader through `pcscd`; if a session stops seeing the card,
reconnecting usually clears it. And upstream FreeRDP has open reports of
`SCARD_E_SHARING_VIOLATION` with some CAC readers
([FreeRDP#13457](https://github.com/FreeRDP/FreeRDP/issues/13457),
[#12778](https://github.com/FreeRDP/FreeRDP/issues/12778)) — if the card
prompt loops inside the session, that is the known upstream issue, not a
configuration problem here. Turn the setting off to connect without it.

Both paths need a reader the host can see:

```bash
systemctl status pcscd     # must be running on the HOST
pcsc_scan -c               # should list your reader with the card inserted
```

Note that OpenSC is pinned to the **PIV-II** driver (`/app/etc/opensc.conf`).
A DoD CAC is both a PIV card and a CAC, and OpenSC 0.26 otherwise prefers its
`cac` driver, which exposes objects labelled "CAC Cert 4"/"CAC Cert 5" with no
identifiable authentication certificate.

## Silent connection sign-in

FreeRDP's `/sec:aad` needs an Entra token at connect time. Rather than let
FreeRDP open its **own** second browser window for that, this app builds
FreeRDP without a webview and drives it over a pseudo-terminal: it intercepts
FreeRDP's `Browse to:` prompt, resolves the URL in a hidden WebKit view that
**shares the sign-in SSO session**, and hands the result back. So the
connection token is acquired with no visible prompt; a window only appears if
MFA/consent is genuinely required (after a short delay). This also removes a
whole class of blank/second-window bugs the bundled FreeRDP webview could hit.

## Tray icon

The app puts an icon in the system tray. Right-click it for every resource,
grouped by workspace (`●` = connected), each with a submenu:
**Connect**, **Connect (windowed)** (a resizable window instead of full
screen), or — for a running session — **Focus window** and **Disconnect**.
Left-click shows the main window. **Quit** from the tray menu exits.

When you connect from the tray with the app hidden, there's a short quiet gap:
after the Microsoft sign-in window (if one is needed) closes, the connection
token is fetched in the background and the session starts with nothing on
screen yet — the app stays in the tray and no window is shown until the remote
desktop itself appears. That pause is normal; it isn't stuck.

Two options in the **Settings** (gear) menu, under **System tray** (both off by default,
so closing the window still quits the app):

- **Keep running in the tray when closed** — the window's close button hides it
  to the tray; sessions and the sign-in refresh keep running.
- **Start in the tray** — with a saved session, the app starts with only the
  tray icon (handy for autostart). A first run still shows sign-in.

The tray works wherever the desktop hosts StatusNotifierItem icons: KDE
Plasma, Xfce, Cinnamon, MATE, LXQt, Budgie, and wlroots/Hyprland bars with a
tray module (e.g. Waybar's `tray`). Stock GNOME needs the *AppIndicator and
KStatusNotifierItem Support* extension (Ubuntu ships it). Without a tray host
the options are greyed out, closing the window quits, and the app always starts
with its window. **Focus window** is a request to the window
manager; on Hyprland it needs `misc:focus_on_activate = true`. Set
`AVD_TRAY=0` to turn the tray off.

## The bundled FreeRDP

The Flatpak builds FreeRDP from upstream `master` with **camera redirection**
(`CHANNEL_RDPECAM_CLIENT`) enabled, plus microphone and multi-monitor.
Automatic reconnect and a gateway keepalive are on by default so brief network
blips and idle timeouts don't drop the session.

It also includes the **PulseAudio hot-unplug + rdpsnd busy-loop fixes**
([FreeRDP#13334](https://github.com/FreeRDP/FreeRDP/pull/13334), now merged
upstream) — without them, changing the audio device mid-call (e.g. plugging
headphones during a Teams call) **freezes the whole session**.

## Install

Clone the repository and build it. Every dependency — SDL3, FreeRDP, OpenSC,
pcsc-lite and the rest — is pinned in the manifest and fetched by
`flatpak-builder`, so there is nothing else to install by hand.

```bash
flatpak install --user flathub org.gnome.Platform//51 org.gnome.Sdk//51 org.flatpak.Builder

git clone https://github.com/btaylor9273/avd_feed_connect.git
cd avd_feed_connect
flatpak run org.flatpak.Builder --user --install --force-clean --ccache build-dir \
    io.github.btaylor9273.avd_feed_connect.yml

flatpak run io.github.btaylor9273.avd_feed_connect
```

The first build takes a while (FreeRDP dominates it). Later builds reuse the
cache — `--force-clean` only clears the staging directory, not the sources.

The manifest builds **the checked-out tree**, so your own changes are picked up
with no extra steps. To build with no network at all, see
`docs/OFFLINE-BUILD.md`, which also covers producing a single `.flatpak` bundle
to hand to someone else.

### If it does not appear in your application menu

The build installs a desktop entry and icons, and Flatpak exports them to
`~/.local/share/flatpak/exports/share`. Your desktop only finds them if that
path is in `XDG_DATA_DIRS`. Flatpak adds it from `/etc/profile.d/flatpak.sh`,
but a session that was already running when Flatpak was installed never picks
it up — the app then only starts from a terminal.

Check:

```bash
echo "$XDG_DATA_DIRS" | tr ':' '\n' | grep flatpak
```

If that prints nothing, **log out and back in**. If it still prints nothing
(some sessions do not read `profile.d`), make it permanent for the systemd user
session:

```bash
mkdir -p ~/.config/environment.d
cat > ~/.config/environment.d/10-flatpak.conf <<'CONF'
XDG_DATA_DIRS=${HOME}/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:${XDG_DATA_DIRS}
CONF
```

then log out and back in.

### What you need on the machine

* A **smart card reader** with `pcscd` running on the host — the app bundles
  OpenSC and the pcsc-lite client library, but not the daemon or the reader
  driver:

  ```bash
  systemctl status pcscd     # must be running
  pcsc_scan -c               # should list your reader with the card inserted
  ```
* An X11 session or XWayland. The bundled FreeRDP SDL client is unstable on
  native Wayland, so connections are forced onto X11.

There is no hosted install or signed update channel for this fork — no
`.flatpakref`, no releases. Build from source.

## Run unpackaged (development)

The same code runs without Flatpak if you have PyGObject (Gtk 4.0, WebKit 6.0)
and an SDL3 `sdl-freerdp` on `PATH` (or at `~/opt/freerdp-sdl3-cam/bin/`):

```bash
python3 src/avd_feed_gui.py           # GUI
python3 src/avdfeed.py list           # CLI: list workspaces
python3 src/avdfeed.py connect 0      # CLI: connect to resource 0
```

Override the tenant/account with `AVD_TENANT` / `AVD_UPN`, and the client binary
with `AVD_SDL_FREERDP`.

> **Use SDL 3.2.x, not a newer system SDL3.** The bundled FreeRDP is built
> against SDL 3.2.30. Running a locally built `sdl-freerdp` against a newer
> distro SDL3 (e.g. Arch's 3.4.x) can make every fullscreen (`/f`) launch fail
> with `Monitor configuration virtual desktop width must be 200 <= 0 <= 32766`
> — SDL reports a 0×0 display. Build SDL 3.2.30 and install it where the app
> looks (`~/opt/sdl3`, or point `AVD_SDL_LIBS` at its `lib` dir):
>
> ```bash
> git clone --depth 1 --branch release-3.2.30 https://github.com/libsdl-org/SDL.git
> cmake -S SDL -B SDL/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
>   -DSDL_STATIC=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF \
>   -DCMAKE_INSTALL_PREFIX=$HOME/opt/sdl3
> cmake --build SDL/build && cmake --install SDL/build
> ```
>
> The app adds `AVD_SDL_LIBS` (default `~/opt/sdl3/lib`) to `LD_LIBRARY_PATH` for
> the client when that directory exists, so it is picked up automatically.

### Connection tuning

The session **auto-adapts to your machine** — it reads your display layout from
the compositor and sets the remote accordingly:

- **Scale** follows your display's HiDPI factor — HiDPI (2×) → `200%`,
  standard/ultrawide (1×) → `100%` — so text isn't tiny or huge.
- **Multi-monitor** follows your actual monitor count — one monitor → single
  fullscreen; two or more → the remote spans them (`/multimon`).

**Most people never need to touch this** — it auto-adapts. If you do want to
override it, the easiest way is right inside the app.

#### In-app settings (no terminal, remembered per workspace)

- **Per workspace:** right-click a workspace tile → set its **Display scale**,
  **Monitors** (single / all / automatic), **Server certificate**, **Session
  authentication**, **Client shortcuts**, and any **Advanced flags** — plus, on a
  **RemoteApp** tile only, **Open RemoteApp as**. These are remembered per
  resource, so a RemoteApp and a full Desktop can differ.
- **Defaults for everything:** the **Settings (gear) menu → Default settings…** sets the
  fallback used by any workspace left on "Automatic", plus the account-wide
  **Sign-in browser** identity.

Precedence is: a workspace's own setting → your Default settings → automatic
detection. (Environment variables, below, override even these — for scripting.)

**Server certificate (verify / don't verify).** By default the app **verifies
the session host's certificate**, pinning it on first connect (FreeRDP's
trust-on-first-use) and refusing the session if that certificate later changes —
protection against a man-in-the-middle. **If you can't connect** — a
"certificate changed" / verification error, common on **pooled** host pools
whose session hosts present different certificates each time, or after a host is
rebuilt — set **Server certificate → Don't verify** for that workspace (or in
Default settings). That restores the old behaviour of accepting any certificate.
Leave it on **Verify** whenever you can; only turn it off if it's actually
blocking you. (Scripting override: `AVD_CERT=ignore` or `AVD_CERT=verify`.)

**Open RemoteApp as (app window / full desktop).** A **RemoteApp** tile is a
single published app (e.g. a line-of-business tool) rather than a full desktop.
The bundled SDL client's RemoteApp/RAIL window integration is limited and the app
window may never appear — the session connects, shows "Loading", and then looks
stuck or drops. If that happens, right-click that tile → **Settings** → set
**Open RemoteApp as → Full desktop**. The app then connects to the host's
**whole desktop** so you can launch the published app from inside it, the way a
desktop tile works.

*Why this route.* True single-window RemoteApp (RAIL) rendering needs a client
that can draw remote app windows on their own; the bundled SDL FreeRDP marks its
RAIL support experimental and, in practice, draws no window for these tiles, so
the session just hangs. Rather than ship a heavier RAIL-capable client to
everyone for one edge case, this option sidesteps RAIL entirely: it rewrites only
that session's downloaded `.rdp` to request a full desktop instead of a published
app, while keeping the RemoteApp's routing info (`RemoteApplicationProgram` and
`loadbalanceinfo`, which Azure Virtual Desktop's endpoint resolver still requires)
intact so AVD can broker the host. It applies to that one RemoteApp tile only, is
**off by default**, and is never shown on Desktop tiles.

> **Backend requirement.** This only works if the RemoteApp's host pool /
> application group will actually hand out a **full desktop** session to your
> account. Many AVD deployments publish an app group as RemoteApp-only and do not
> grant desktop access on the same hosts — there the full-desktop request is
> refused and you will not connect. If you can't log in after switching, the host
> isn't offering a desktop and this toggle can't help; you'd need the admin to
> grant desktop access (or a RAIL-capable client).

(Scripting override: `AVD_REMOTEAPP=desktop`.)

**Client shortcuts (Right Shift + key).** The bundled SDL client reserves
Right Shift + key for its own shortcuts (D disconnects, Enter toggles
fullscreen, R resizable, G keyboard grab, M minimize), so those keys never reach
the remote desktop — and if Right Shift is seen as held (e.g. a stuck modifier
under XWayland), an ordinary Ctrl+D can end the session. Set **Client
shortcuts → Disabled** to pass every key through. The choice is written to
FreeRDP's own `~/.config/freerdp/sdl-freerdp.json` (`SDL_KeyModMask`) on each
connect; **Automatic** leaves that file untouched.

**Sign-in browser (if your organization blocks Linux at sign-in).** Some
tenants have a Conditional Access policy that only lets you sign in from a
**Windows device** or **Microsoft Edge**, and it decides that from the browser
identity (User-Agent) the sign-in sends. The built-in sign-in window is Linux
WebKit, so such a tenant blocks it — the same wall you hit in a web browser
until you install a "User-Agent switcher" extension. This setting is that
switch, built in: in **⋯ menu → Default settings…** set **Sign-in browser** to
**Microsoft Edge on Windows** (or **Google Chrome on Windows**) and every
sign-in window presents that identity instead, which is enough to satisfy those
policies. **Custom…** lets you paste any User-Agent string verbatim.

Leave it on **Automatic** unless a sign-in is actually being blocked —
**Automatic** is the normal Linux identity and is what everyone who isn't behind
such a policy should keep.

It is a single, **account-wide** choice (so it lives in Default settings, not on
individual workspaces): the first account sign-in and every connection share one
signed-in session, so they must all present the **same** identity — if they
differed, Microsoft would see an inconsistent device for that session and refuse
to connect. The setting changes only the sign-in window's identity — not the
remote session — and takes effect on your next sign-in.

**Resizable window (dynamic resolution):** by default the session opens
fullscreen. If you'd rather have a resizable window whose remote resolution
follows the window as you resize it, add `/dynamic-resolution` to that
workspace's **Advanced flags**. The session then starts windowed (toggle
fullscreen any time with Ctrl+Shift+Enter). Fullscreen and `/dynamic-resolution`
can't be combined, so the app only forces fullscreen when this flag isn't set;
it also clears the feed's smart-sizing property, which would otherwise conflict.
Dynamic resolution runs on a **single monitor** — when it's set, the app ignores
multi-monitor selection for that session (leave it off to span monitors).

**Advanced flags reference.** The **Advanced flags** field takes raw
`sdl-freerdp` options, appended verbatim to the connection. Anything FreeRDP
accepts works; these are the ones people reach for most:

| Flag | What it does |
|---|---|
| `/dynamic-resolution` | Open a resizable window; the remote resolution follows the window instead of a fixed fullscreen size (see above). |
| `/d:AzureAD` | Override the NLA logon domain (the app sends an empty domain by default; a few host pools want `AzureAD` or an on-prem domain instead). |
| `/multimon` / `-multimon` | Force multi-monitor on / off, overriding the automatic monitor-count choice. Same as the **Monitors** setting. |
| `/gfx` | Force the GFX (RemoteFX/H.264) graphics pipeline — smoother video on capable host pools. |
| `/network:auto` | Let FreeRDP auto-detect the link and tune codecs/latency for it. |
| `-themes` / `-wallpaper` | Drop remote desktop themes / wallpaper to save bandwidth on slow links. |

Flags set here take precedence over the app's automatic choices, and
`AVD_EXTRA_ARGS` (below) is appended after them for scripting.

**How to enter them.** Type the flags into the **Advanced flags** field exactly
as you would on a command line — **separated by spaces**, and you can combine as
many as you like. Flags that take a value use a colon, with no space around it.
Examples (each line is what you'd put in the field):

```
/dynamic-resolution
```
Resizable window, remote follows the window size.

```
/dynamic-resolution /gfx
```
Resizable window **and** the H.264 graphics pipeline — two flags, space-separated.

```
/d:AzureAD
```
Force the NLA logon domain to `AzureAD` (for a host pool that rejects the empty
default).

```
/dynamic-resolution /gfx -wallpaper -themes
```
A slow-link profile: resizable window, GFX codec, and strip the remote wallpaper
and themes to save bandwidth — four flags at once.

If a value ever contains a space, quote it like on a shell (e.g.
`/drive:home,"/my folder"`); the field is parsed with the same rules.

#### Advanced: environment-variable overrides

These are for power users / scripting and win over the in-app settings. Set them
graphically (no terminal) with [**Flatseal**](https://flathub.org/apps/com.github.tchx84.Flatseal):
install it from your software center (search "Flatseal"), pick **AVD Feed +
Connect Linux**, open the **Environment** section, and add a line like
`AVD_SCALE=150`. Or from a terminal:

```bash
flatpak override --user --env=AVD_SCALE=150 io.github.btaylor9273.avd_feed_connect
```

Both methods write the same setting.

#### The variables

| Variable | Effect |
|---|---|
| `AVD_SCALE` | Force the remote scale percentage (`100`, `125`, `150`, `200`, …). Overrides the auto HiDPI detection. |
| `AVD_MULTIMON` | Force multi-monitor on (`1`/`on`) or off (`0`/`off`). Overrides the auto monitor-count detection. |
| `AVD_REMOTEAPP` | `desktop` opens **every** RemoteApp tile as the host's full desktop instead of the app window (the per-tile **Open RemoteApp as** setting does the same for one tile). |
| `AVD_EXTRA_ARGS` | Extra `sdl-freerdp` flags appended verbatim, e.g. `"/gfx"`, `"/network:auto"`, or your own `/multimon` / `-multimon` (which then wins over the auto choice). |
| `AVD_SDL_FREERDP` | Path to the `sdl-freerdp` binary (defaults to the bundled one). |
| `AVD_TRAY` | `0`/`off` disables the tray icon (closing the window then always quits). |

Connections are launched over X11/XWayland (`GDK_BACKEND=x11`) because FreeRDP's
SDL client is unstable on native Wayland; this is automatic.

## Signing in — and why you might be asked again

The app keeps you signed in the way the Windows App does: your workspaces (and
their icons) are saved and shown instantly on launch, and the access token is
renewed silently in the background with the refresh token. Normally you only
see the sign-in page on first run or after **⋯ → Sign out**.

If you are instead asked to sign in **every time** you open the app or connect,
that is your organization's **Conditional Access sign-in frequency** policy,
not the app. The tell-tales are the status bar ("your organization requires
signing in again (Conditional Access sign-in frequency)") and, in a terminal,
`token refresh failed … AADSTS70043 … maximum allowed lifetime for this request
is 300` — Entra refuses to renew the Azure Virtual Desktop token unless you
signed in within the last *N* minutes (300 s is the "Every time" setting). No
client can renew silently under that rule; the official clients usually avoid
the prompt only because the policy excludes managed/compliant devices, which a
Linux machine typically isn't. Your admin can see which policy fired under
Entra → Sign-in logs.

The app makes that as painless as it can: the saved workspaces stay on screen,
and because your Microsoft SSO session is remembered between launches, the
re-auth is usually a **single click** on your account (no password re-entry)
rather than a full password+MFA — unless the policy is strict enough to demand
fresh credentials. The workspace you double-clicked then connects on its own
once you're back in.

## Host pools that use NLA (username + password)

Some AVD host pools aren't set up for **Microsoft Entra ID RDP authentication**
(their feed `.rdp` has no `enablerdsaadauth`). The app detects this and connects
with **NLA** instead of the token flow: it passes your account as the username
and an **empty domain**, and FreeRDP prompts you for your password each time you
connect (it isn't stored). Your Entra sign-in is still used for the feed and the
gateway — the password is only for the session-host logon.

- **Hybrid (Active Directory) joined** hosts work this way with your normal
  domain password.
- A **pure Entra-ID-joined** host that requires NLA **can't be reached from
  Linux** — that path (PKU2U) needs Windows-only components. The fix is to have
  your admin enable *Microsoft Entra ID authentication* on the host pool, after
  which sign-in is token-based with no password prompt.
- If your host pool needs a specific domain instead of the empty default, set it
  in a workspace's **Advanced flags** (e.g. `/d:AzureAD`).

## Microsoft Teams calls

Teams **chat, file sharing and calls work**, using your redirected microphone
and camera (a Bluetooth headset works too — audio follows whatever your host's
default PulseAudio device is).

Calls run **on the session host**, not locally, so there is no "AVD Media
Optimized" banner. That only works if the host can reach Teams' media relays
directly — outbound **UDP 3478–3481** to the Teams/Microsoft 365 media IP
ranges. On host pools where that egress is locked down, calls started inside
the session fail to connect (Teams logs a `MediaWhitelistingIssue` / `no
relays` ICE error) even though the same account places calls fine from the
Windows App, where media is redirected to the local device instead.

### Why there is no media optimization here

Microsoft's Teams optimization — the *WebRTC Redirector* that hands call media
to the client over a dedicated virtual channel — ships only in the Windows App
and Microsoft Remote Desktop clients, and upstream FreeRDP has no such channel.

Upstream bundles [**freerdp-teams-redir**](https://github.com/vchegwidden/freerdp-teams-redir),
an unofficial add-in that implements the client side of it. **This fork does
not bundle it**: that module builds with network access and resolves Rust
crates plus a prebuilt libwebrtc binary at build time, so it cannot be built
air-gapped and its dependency set is not pinned or auditable — see
`docs/DEPENDENCIES.md`. The **Settings → Optimize Teams calls** options remain
visible but do nothing without the plugin.

If host-side calling is blocked for you, the options are to ask your admins to
permit the session host's outbound UDP to the Teams relays, place the call from
a Teams client outside the VM, or maintain a separate build that bundles the
add-in (accepting that it is not air-gappable).

## Status

Working: feed discovery, keyring-backed sign-in with click-through re-auth,
CAC/PIV certificate sign-in, Azure US Government endpoints, the workspace grid,
silent connect, and camera/mic/multi-monitor redirection. Feedback welcome.

## License

MIT for this app's code. Bundled components keep their own licenses — see
`docs/DEPENDENCIES.md` for the full inventory with versions and pinned hashes.

### Optional GNOME connection workspaces: switching between desktops

Put each desktop connection on its **own GNOME workspace**, so moving between
your local desktop and the remote one is `Super + PageUp/PageDown` (or a
three-finger swipe) rather than window switching. The app follows you to the
session's workspace once logon completes.

This needs two things: the companion GNOME Shell extension, and the setting.

```bash
# 1. Install the extension from this checkout.
cp -r gnome-extension/avd-workspaces@avd-feed-connect \
      ~/.local/share/gnome-shell/extensions/

# 2. Make GNOME Shell notice it.
#    X11:     Alt+F2, type r, Enter  (your windows survive)
#    Wayland: log out and back in
# 3. Enable it.
gnome-extensions enable avd-workspaces@avd-feed-connect
gnome-extensions info avd-workspaces@avd-feed-connect | grep State   # -> ACTIVE
```

Then turn on **Default settings → Start connection in a new virtual desktop**.
A resource's own settings can inherit that, enable it, or disable it. Remote
apps are unaffected, and the default is off.

The extension is required on GNOME generally, not only on Wayland: the app
places windows by calling the extension over D-Bus, never through Shell `Eval`
or X11 workspace hacks. Without it — or on another desktop — connections open
normally and the option is hidden. Installing and enabling it are deliberate
user actions; the app never does either by itself. The Flatpak manifest carries
permission for the extension's dedicated D-Bus name.

**If the app does not appear in GNOME Extensions**, check the `shell-version`
list in `metadata.json` includes your GNOME Shell version (`gnome-shell
--version`). GNOME silently ignores extensions that do not list it.

Placement uses an exact random FreeRDP window title rather than resource names
or sandbox PIDs. It waits briefly for the desktop window after logon, reuses an
empty trailing workspace when available, and otherwise appends one. Reconnects
and focus actions reuse the session; failed launches create no workspace.
No global workspace settings are changed and no workspace is deleted: GNOME
reclaims empty dynamic workspaces normally; with fixed workspaces, an existing
empty workspace is reserved, or placement falls back to the current workspace
if none is available. The configured number of workspaces is preserved. On secondary monitors GNOME's existing
“Workspaces on primary display only” preference still applies. Display hotplug
windows created later by FreeRDP are outside the initial placement attempt.

### UI translations

Select **Settings → Language** to use the system language or choose English,
Latin American Spanish, Spain Spanish, or Argentina Spanish. Changes are saved
and applied immediately, including open settings and tray menus. System selection
uses `LANGUAGE` (first preference), then `LC_ALL`, `LC_MESSAGES`, and `LANG`;
`C`/`POSIX` message locales always use English. Latin American Spanish locales
map to `es-419`; unsupported languages and regions use English.

English strings stay in Python source. UTF-8 JSON catalogs in
`src/avd_feed_connect/locales/` use the English string as their key. `es.json`
is deliberately shared by all three Spanish variants; regional files contain
only wording overrides. A missing or empty translation falls back to the
English source. Keep named `{placeholders}`, newlines and technical identifiers
intact. The supported languages use singular for one and plural otherwise.
To add or update translations, edit those catalogs and validate with:

```sh
python -m pytest tests/test_i18n.py
python -m pip wheel --no-deps .
```

The catalog tests check source coverage and placeholder consistency. Setuptools
includes catalogs as package data; the Flatpak manifest copies the entire
Python package, including these catalogs, without a separate compilation step.
