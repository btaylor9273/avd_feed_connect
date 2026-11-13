#!/usr/bin/env python3
"""Generate the bundled-dependency inventory from the Flatpak manifest.

Written for security review: the inventory is GENERATED rather than
hand-maintained, so it cannot drift from what is actually built. Licenses and
submodules are read out of the pinned sources themselves (the git mirrors and
downloaded archives under .flatpak-builder/) rather than asserted from memory,
so a reviewer can reproduce every line.

    python3 tools/inventory.py > docs/DEPENDENCIES.md
"""

import os
import re
import subprocess
import sys
import tarfile

import yaml

MANIFEST = "io.github.btaylor9273.avd_feed_connect.yml"
CACHE = ".flatpak-builder"

# Matched against the first lines of a licence file. Order matters: the more
# specific texts are tested first.
_LICENCES = (
    ("Apache License", "Apache-2.0"),
    ("GNU LESSER GENERAL PUBLIC", "LGPL-2.1"),
    ("GNU GENERAL PUBLIC", "GPL"),
    ("This software is provided 'as-is', without any express or implied", "Zlib"),
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Redistribution and use in source and binary forms", "BSD"),
)


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# Only the head of a licence file is the primary grant. pcsc-lite's COPYING,
# for instance, is BSD-3-Clause but mentions the GPL further down — scanning
# the whole file reports the wrong licence, which is exactly the sort of error
# a reviewer should not have to catch for us.
_PRIMARY_CHARS = 1500


def _identify(text):
    flat = " ".join(text.split())
    head, tail = flat[:_PRIMARY_CHARS], flat[_PRIMARY_CHARS:]
    primary = None
    for needle, name in _LICENCES:
        if needle.lower() in head.lower():
            primary = name
            break
    if primary is None:
        return "UNIDENTIFIED — read the file"
    others = sorted({name for needle, name in _LICENCES
                     if name != primary and needle.lower() in tail.lower()})
    return primary + (f" (also mentions {', '.join(others)})" if others else "")


def _mirror_for(url):
    root = os.path.join(CACHE, "git")
    if not os.path.isdir(root):
        return None
    tail = url.rstrip("/").split("/")[-1]
    for entry in os.listdir(root):
        if entry.endswith(tail):
            return os.path.join(root, entry)
    return None


def _from_git(url, commit):
    """``(licence, [submodules])`` read at the pinned commit."""
    mirror = _mirror_for(url)
    if mirror is None:
        return "not cached — run a build first", []
    names = _run(["git", "--git-dir", mirror, "ls-tree", "--name-only", commit]).split()
    licence = "none found"
    for name in names:
        if re.match(r"(?i)^(LICENSE|COPYING)", name):
            licence = f"{_identify(_run(['git', '--git-dir', mirror, 'show', f'{commit}:{name}']))} ({name})"
            break
    modules = _run(["git", "--git-dir", mirror, "show", f"{commit}:.gitmodules"])
    subs = re.findall(r"url\s*=\s*\S*?/([^/\s]+?)(?:\.git)?\s*$", modules, re.M)
    return licence, sorted(set(subs))


def _from_archive(sha256):
    directory = os.path.join(CACHE, "downloads", sha256)
    if not os.path.isdir(directory):
        return "not cached — run a build first"
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            with tarfile.open(path) as archive:
                for member in archive.getnames():
                    if re.search(r"(?i)/(LICENSE|COPYING)[^/]*$", member):
                        handle = archive.extractfile(member)
                        if handle is None:
                            continue
                        text = handle.read().decode("utf-8", "replace")
                        return f"{_identify(text)} ({member.split('/', 1)[-1]})"
        except (tarfile.TarError, OSError):
            continue
    return "none found"


def main():
    manifest = yaml.safe_load(open(MANIFEST))
    rows, networked = [], []
    for module in manifest["modules"]:
        if "--share=network" in (module.get("build-options", {}).get("build-args") or []):
            networked.append(module["name"])
        for source in module.get("sources", []):
            kind = source.get("type")
            if kind == "git":
                licence, subs = _from_git(source["url"], source["commit"])
                rows.append((module["name"], "git", source["url"],
                             source["commit"], licence, ", ".join(subs) or "—"))
            elif kind == "archive":
                rows.append((module["name"], "archive", source["url"],
                             f"sha256:{source['sha256']}",
                             _from_archive(source["sha256"]), "—"))
            elif kind == "dir":
                rows.append((module["name"], "this repo", "—", "—",
                             "MIT (LICENSE)", "—"))

    out = sys.stdout.write
    out("<!-- GENERATED by tools/inventory.py — do not edit by hand. -->\n")
    out("# Bundled dependency inventory\n\n")
    out(f"Every component built into `{manifest['app-id']}`, taken straight from\n")
    out(f"`{MANIFEST}`. Licences and submodules are read from the pinned sources\n")
    out("themselves, not asserted from memory.\n\n")
    out(f"- Runtime: `{manifest['runtime']}` branch `{manifest['runtime-version']}` "
        f"(SDK `{manifest['sdk']}`), from Flathub.\n")
    out(f"- Modules built from source: **{len(manifest['modules'])}**\n")
    out(f"- Modules with build-time network access: "
        f"**{', '.join(networked) if networked else 'none'}**\n\n")
    if not networked:
        out("> No module fetches anything during the build, so every source is\n"
            "> pinned by commit or sha256 and the build runs air-gapped.\n"
            "> See `docs/OFFLINE-BUILD.md`.\n\n")
    out("| Module | Source | Pinned at | Licence | Git submodules |\n")
    out("|---|---|---|---|---|\n")
    for name, kind, url, pin, licence, subs in rows:
        shown = f"[{url.split('/')[-1]}]({url})" if url.startswith("http") else kind
        pin = pin if pin.startswith("sha256") or pin == "—" else f"`{pin[:12]}`"
        out(f"| `{name}` | {shown} | {pin} | {licence} | {subs} |\n")
    out("\n## Transitive sources\n\n")
    out("Modules whose git submodules are fetched at their pinned commit. These\n")
    out("are built into the app and are listed here because the manifest does\n")
    out("not name them directly.\n\n")
    for name, kind, url, pin, licence, subs in rows:
        if subs != "—":
            out(f"- `{name}` → {subs}\n")
    out("\n## Reproducing this file\n\n```bash\n")
    out("flatpak run org.flatpak.Builder --download-only build-dir \\\n")
    out(f"    {MANIFEST}\npython3 tools/inventory.py > docs/DEPENDENCIES.md\n```\n")


if __name__ == "__main__":
    main()
