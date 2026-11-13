# Building without network access

No module in this manifest fetches anything at build time — every source is
pinned by git commit or sha256 (see `docs/DEPENDENCIES.md`). So the build can
run on an isolated host, provided the sources and the runtime are carried in.

Two things have to cross the air gap: the **sources** and the **GNOME
runtime/SDK**.

## On a connected machine

```bash
# 1. Build tooling and the runtime this app targets.
flatpak install --user -y flathub \
    org.flatpak.Builder org.gnome.Platform//51 org.gnome.Sdk//51

# 2. Download every pinned source without building anything.
cd avd_feed_connect
flatpak run org.flatpak.Builder --download-only build-dir \
    io.github.btaylor9273.avd_feed_connect.yml

# 3. Package the repo together with the populated source cache.
#    .flatpak-builder/{downloads,git} is the cache; the rest is build output.
tar czf avd-feed-connect-sources.tar.gz \
    --exclude='.flatpak-builder/build' \
    --exclude='.flatpak-builder/cache' \
    --exclude='.flatpak-builder/ccache' \
    --exclude='.flatpak-builder/rofiles' \
    --exclude='build-dir' \
    .

# 4. Export the runtime, SDK and builder for the isolated host.
flatpak create-usb --allow-partial /media/transfer \
    org.gnome.Platform//51 org.gnome.Sdk//51 org.flatpak.Builder
```

Carry `avd-feed-connect-sources.tar.gz` and the `/media/transfer` contents
across.

## On the isolated machine

```bash
# 1. Install the runtime, SDK and builder from the transfer medium.
flatpak install --user --sideload-repo=/media/transfer/.ostree/repo \
    org.gnome.Platform//51 org.gnome.Sdk//51 org.flatpak.Builder

# 2. Unpack and build with downloads disabled. If anything were missing or had
#    drifted from its pin, the build FAILS here rather than reaching out.
mkdir avd-feed-connect && tar xzf avd-feed-connect-sources.tar.gz -C avd-feed-connect
cd avd-feed-connect
flatpak run org.flatpak.Builder --user --install --force-clean --disable-download \
    build-dir io.github.btaylor9273.avd_feed_connect.yml

flatpak run io.github.btaylor9273.avd_feed_connect
```

`--disable-download` is the thing worth noting for a reviewer: it makes the
build fail rather than silently fetch, so a successful offline build is
evidence that nothing came from the network.

## Verifying the sources independently

The cache is content-addressed by the hash in the manifest, so an archive can
be checked without trusting this repo:

```bash
sha256sum .flatpak-builder/downloads/*/*
```

Each directory name **is** the expected sha256 of the file inside it. Git
modules are bare mirrors under `.flatpak-builder/git/`; verify one with:

```bash
git --git-dir=.flatpak-builder/git/<mirror>.git log -1 <commit-from-manifest>
```

## Producing a redistributable bundle

To hand peers a single file instead of having each of them build:

```bash
flatpak run org.flatpak.Builder --repo=repo --force-clean --disable-download \
    build-dir io.github.btaylor9273.avd_feed_connect.yml
flatpak build-bundle repo avd-feed-connect.flatpak \
    io.github.btaylor9273.avd_feed_connect
```

Peers then install with:

```bash
flatpak install --user avd-feed-connect.flatpak
```

They still need `pcscd` running and a reader on their own machine — the app
bundles OpenSC but not the daemon or the reader driver.

## Host prerequisites for running it

```bash
systemctl status pcscd     # must be running
pcsc_scan -c               # should list the reader with the card inserted
```
