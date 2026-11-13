# Sandbox permissions and credential handling

Written for security review. Every sandbox permission the app requests is
listed with why it is needed and what it does *not* grant, followed by exactly
what happens to the smart card PIN, the Entra tokens and the keyring.

The app runs as a Flatpak, so it has **no** access to anything not listed here:
no home directory, no arbitrary filesystem, no host processes. Its own data
lives under `~/.var/app/<app-id>/`.

## Permissions

| Permission | Why | Notes |
|---|---|---|
| `--share=network` | Entra sign-in, feed discovery, and the RDP gateway connection. | Destinations are constrained in code — see *Where the token may be sent*. |
| `--share=ipc` | X11 shared memory for the remote-desktop window. | Standard for X11 clients; no host IPC beyond that. |
| `--socket=x11` | The GUI and the FreeRDP session window. | The bundled FreeRDP SDL client is unstable on native Wayland, so sessions are forced onto X11/XWayland. X11 does not isolate input between clients. |
| `--socket=pulseaudio` | Audio playback and microphone redirection into the session. | |
| `--device=dri` | GPU acceleration for the remote-desktop graphics pipeline. | |
| `--device=all` | Webcam redirection, and USB access for FIDO2/WebAuthn redirection. | **Broadest permission requested.** It grants access to all devices, not just the camera. Needed because the camera and FIDO2 channels are compiled in. Remove it if your posture forbids it — the app still signs in and connects; camera and security-key redirection stop working. |
| `--socket=pcsc` | Smart card access, via the **host's** `pcscd`. | Flatpak binds the daemon socket in and sets `PCSCLITE_CSOCK_NAME`. No reader driver is bundled; the host owns the reader. |
| `--talk-name=org.freedesktop.secrets` | Stores the OAuth refresh token in the login keyring. | Without it the token falls back to a `0600` file. |
| `--talk-name=org.kde.StatusNotifierWatcher` | Optional tray icon. | |
| `--talk-name=org.freedesktop.Notifications` | Desktop notifications. | |
| `--talk-name=…AvdWorkspaces` | Optional GNOME Shell extension for workspace placement. | Inert if the extension is absent. |
| `--env=OPENSC_CONF`, `--env=LD_LIBRARY_PATH` | Point OpenSC at the bundled config and resolve bundled libraries. | Not privileges. |

Deliberately **not** requested: `--filesystem=*` of any kind, `--device=shm`,
`--socket=wayland`, `--talk-name=org.freedesktop.Flatpak` (sandbox escape),
`--system-talk-name`, `--allow=devel`.

## The smart card

Certificate-based Entra sign-in needs the card to sign the TLS handshake.

- The app reads **only the certificate** off the card. The private key never
  leaves it: GnuTLS has the card perform the signature via PKCS#11.
- The card is reached through the host's `pcscd` and the bundled OpenSC
  PKCS#11 module. The module is registered with p11-kit at startup by writing
  `/etc/pkcs11/modules/opensc.module` — inside the sandbox `/etc` is a
  per-instance tmpfs, so this touches nothing on the host and does not persist.
- The credential used is the **PIV Authentication** certificate (PIV slot 9A),
  chosen because it is the card's TLS client-authentication credential. The
  driver is pinned to `PIV-II` in `/app/etc/opensc.conf`.

### The PIN

- Held **in memory only**, for the lifetime of the process.
- Never written to settings, never to the keyring, never to a log, and never
  to the pseudo-terminal that the per-connection session logs record.
- Cleared on sign-out and when the card reports a failed attempt, so a wrong
  cached PIN is never retried automatically — a CAC locks after a few failures.
- Entered in a password-masked dialog; `FINAL_TRY` from the card is surfaced
  as a warning.

## Tokens

- **Refresh token**: stored in the login keyring via libsecret, namespaced per
  account. If the keyring is unavailable it falls back to a `0600` file in the
  app's own data directory, and a plaintext file left by an older version is
  migrated into the keyring and then shredded.
- **Access token**: memory only, renewed silently via the OAuth2
  `refresh_token` grant.
- **Sign-in flow**: authorization code + PKCE (S256). The `state` parameter is
  generated with `secrets.token_urlsafe(16)` and checked before the code is
  used. The code verifier never leaves process memory.

### Where the token may be sent

A bearer token is attached only to `https` URLs on an allow-list, matched on a
dot boundary so look-alike hosts are rejected. The list is per account and per
cloud:

- Commercial: `.microsoft.com`, `.microsoftonline.com`
- US Government: those plus `.microsoftonline.us`, `.wvd.azure.us`

Feed XML supplies the per-tenant feed, `.rdp` and icon URLs, so each is checked
before the `Authorization` header is attached. Broad suffixes are deliberately
**excluded**: `.azure.us` and `.usgovcloudapi.net` contain customer-registrable
hostnames (for example `<account>.blob.core.usgovcloudapi.net`) that hold valid
Microsoft TLS certificates, so trusting them would let a compromised feed
redirect the token to a third party. `.mil` is likewise not trusted — it is a
whole top-level domain.

A second, stricter allow-list governs which URL may be loaded into the WebKit
view that holds the Microsoft SSO cookies: exact host equality against
`login.microsoftonline.{com,us}` and `certauth.login.microsoftonline.{com,us}`
only. The `certauth` hosts are where Entra performs certificate-based auth.

## Data at rest

Everything lives under `~/.var/app/<app-id>/data/avd-feed-connect/`, with
directories created `0700` and files `0600`:

- `accounts.json` — account identities (tenant, object id, UPN, cloud).
- `accounts/<id>/token-cache.json` — only if the keyring is unavailable.
- `accounts/<id>/feed/` — downloaded `.rdp` files and per-connection logs.
- `accounts/<id>/webview/` — Microsoft SSO cookies, so re-auth is a click.
- `settings.json` — UI preferences. Contains no credentials.
