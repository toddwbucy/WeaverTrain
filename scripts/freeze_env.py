"""Print the installed-package lock for the running interpreter's environment.

One line per installed distribution (the editable weaver-train itself excluded):

    <name>==<version>  build=<16 hex>  [source=<url or index>]

``build`` is the sha256 of the distribution's RECORD hashes (every installed file's
own hash), so two builds of the same version -- e.g. xformers for CUDA 12.8 versus
13.0, both "0.0.35" -- get different lines. ``pip freeze`` cannot tell them apart.

setup-env.sh writes this to environment.lock on first run and diffs it on every run.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import platform
import sys


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


def main() -> int:
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
    print("# weaver-train environment lock: what is actually installed in .venv.")
    print("# Resolution comes from uv.lock; this file pins the result down to the build")
    print("# (build = hash of each package's installed files, less its generated bin/")
    print("# scripts). scripts/setup-env.sh checks the venv against it on every run;")
    print("# change it only with --update-lock.")
    print(f"# python {platform.python_version()} {sys.platform} {platform.machine()}")
    for line in sorted(set(lines)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
