"""Print the installed-package lock for the running interpreter's environment.

One line per installed distribution (the editable weaver-train itself excluded):

    <name>==<version>  build=<16 hex>  [source=<url or index>]

``build`` is the sha256 of the distribution's RECORD hashes (every installed file's
own hash), so two builds of the same version -- e.g. xformers for CUDA 12.8 versus
13.0, both "0.0.35" -- get different lines. ``pip freeze`` cannot tell them apart.

setup-env.sh writes this to environment.lock on first run and diffs it on every run.

The lock alone cannot see a file changed after install: RECORD keeps the hash the
installer wrote. ``--check-files`` hashes every installed file against its RECORD entry
and fails on any difference that config/known-modified-files.toml does not list (a
listed file may hold its RECORD content or the one recorded modified content). Found
2026-10-31: importing unsloth rewrites mamba_ssm's ssd_chunk_scan.py on disk.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import base64
import platform
import sys
import tomllib
from pathlib import Path

ALLOW = Path(__file__).resolve().parents[1] / "config" / "known-modified-files.toml"


def build_id(dist: md.Distribution) -> str:
    record = dist.read_text("RECORD") or ""
    hashes = sorted(
        line.rsplit(",", 2)[0] + "," + line.rsplit(",", 2)[1]
        for line in record.splitlines()
        if line.count(",") >= 2 and line.rsplit(",", 2)[1]  # skip entries with no hash
        and ".dist-info/" not in line.rsplit(",", 2)[0]  # installer metadata varies
        # entry-point scripts are generated at install with the venv's absolute path
        # in the shebang, so they change when the venv moves; the package files do not
        and not line.startswith("../../../bin/")
    )
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()[:16]


def source(dist: md.Distribution) -> str | None:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    d = json.loads(raw)
    if d.get("dir_info", {}).get("editable"):
        return "editable"
    return d.get("url")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def check_files(env: str) -> int:
    """Hash every installed file against RECORD; report what the allowlist does not cover."""
    allow = {(e["dist"].lower(), e["path"]): e
             for e in tomllib.loads(ALLOW.read_text()).get("file", []) if e["env"] == env}
    seen, problems, checked = set(), [], 0
    # a path two wheels both ship (flashinfer-python and torch_c_dlpack_ext each install a
    # top-level build_backend.py): the last install wins, so either owner's hash is valid
    owners: dict[str, set[str]] = {}
    for dist in md.distributions():
        for f in dist.files or []:
            if f.hash:
                owners.setdefault(str(f), set()).add(f.hash.value)
    for dist in md.distributions():
        if source(dist) == "editable":
            continue
        name = dist.metadata["Name"].lower()
        for f in dist.files or []:
            rel = str(f)
            if not f.hash or f.hash.mode != "sha256" or rel.startswith("../../../bin/"):
                continue
            path = Path(dist.locate_file(f))
            checked += 1
            if not path.exists():
                problems.append(f"missing  {name} {rel}")
                continue
            data = path.read_bytes()
            if _b64(data) in owners.get(rel, {f.hash.value}):
                continue
            entry = allow.get((name, rel))
            if entry and hashlib.sha256(data).hexdigest() == entry["sha256"]:
                seen.add((name, rel))
                continue
            problems.append(f"modified {name} {rel} sha256={hashlib.sha256(data).hexdigest()}"
                            + ("  (listed, but with other content)" if entry else ""))
    for p in problems:
        print(p, file=sys.stderr)
    print(f"{checked} files checked; {len(seen)} known modifications present; "
          f"{len(problems)} unexplained", file=sys.stderr)
    return 1 if problems else 0


def known_state(env: str) -> int:
    """sha256 on disk of each file the allowlist names for env: RECORD cannot see a
    listed rewrite, so the verify stamp keys on this."""
    for e in tomllib.loads(ALLOW.read_text()).get("file", []):
        if e["env"] != env:
            continue
        try:
            path = Path(md.distribution(e["dist"]).locate_file(e["path"]))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except (md.PackageNotFoundError, FileNotFoundError):
            digest = "absent"
        print(f"{e['dist']} {e['path']} {digest}")
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--known-state"]:
        return known_state(sys.argv[2] if len(sys.argv) > 2 else "training")
    if sys.argv[1:2] == ["--check-files"]:
        return check_files(sys.argv[2] if len(sys.argv) > 2 else "training")
    lines = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        src = source(dist)
        if src == "editable":
            continue
        line = f"{name.lower()}=={dist.version}  build={build_id(dist)}"
        if src:
            line += f"  source={src}"
        lines.append(line)
    print("# weaver-train environment lock: installed builds in .venv.")
    print("# Resolution comes from uv.lock. This file pins the result down to the build")
    print("# (build = hash of each package's installed files, less its generated bin/")
    print("# scripts). scripts/setup-env.sh checks the venv against it on every run.")
    print("# change it only with --update-lock.")
    print(f"# python {platform.python_version()} {sys.platform} {platform.machine()}")
    for line in sorted(set(lines)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
