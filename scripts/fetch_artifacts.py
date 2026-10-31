"""Fetch, verify and stage pinned Hugging Face repositories: models, benchmarks, datasets.

    python scripts/fetch_artifacts.py --set models        # config/models.toml
    python scripts/fetch_artifacts.py --set benchmarks    # config/benchmarks.toml
    python scripts/fetch_artifacts.py --set datasets      # config/datasets.toml

| set        | config                  | manifests              | store env var             | cache env var            |
|------------|-------------------------|------------------------|---------------------------|--------------------------|
| models     | config/models.toml      | manifests/models/      | WEAVER_TRAIN_MODEL_STORE  | WEAVER_TRAIN_MODEL_CACHE |
| benchmarks | config/benchmarks.toml  | manifests/sources/     | WEAVER_TRAIN_BENCH_STORE  | (none)                   |
| datasets   | config/datasets.toml    | manifests/datasets/    | WEAVER_TRAIN_DATA_STORE   | (none)                   |

The datasets set also writes a manifest of the whole store: every file of every entry
in config/datasets.toml, with paths relative to the store root. It is composed from the
per-entry manifests (no re-hash), committed as manifests/datasets/STORE.json and
written read-only to <store>/MANIFEST.json. ``verify_store`` checks the store against it.

Each entry pins ``repo_id``, ``revision`` and ``local_dir``; optional ``repo_type``
(default "model") and ``allow_patterns`` (only those files are fetched and manifested).

Two stages per entry, each skipped when already done:

store  Download the pinned revision into <store>/<local_dir>.
       First time: hash every file and check it against the publisher's own digest
       (LFS sha256, or git blob sha1 for small files), then write
       manifests/<set dir>/<name>.json. Afterwards: full sha256 check against that
       manifest. Files are then made read-only.
cache  (models only) Copy the verified tree to <cache>/<local_dir>, the fast-load copy
       the loader reads; full sha256 check, read-only. Skipped if the cache is unset.

"Already done" is a stamp in $WEAVER_TRAIN_STATE_DIR/stamps recording the verified
tree hash plus each file's size and mtime. A matching stamp skips re-hashing; any
size/mtime change, missing or extra file voids it and forces a full check.
--rehash ignores stamps.

A tree that is complete but fails its manifest is never repaired automatically:
the script stops and reports, because a reference artifact has changed.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from weaver_train import manifest as mf  # noqa: E402


SETS = {
    "models": {"config": "config/models.toml", "manifests": "manifests/models", "kind": "model",
               "stamp": "model", "store": "WEAVER_TRAIN_MODEL_STORE", "cache": "WEAVER_TRAIN_MODEL_CACHE"},
    "benchmarks": {"config": "config/benchmarks.toml", "manifests": "manifests/sources", "kind": "source",
                   "stamp": "source", "store": "WEAVER_TRAIN_BENCH_STORE", "cache": None},
    "datasets": {"config": "config/datasets.toml", "manifests": "manifests/datasets", "kind": "dataset",
                 "stamp": "dataset", "store": "WEAVER_TRAIN_DATA_STORE", "cache": None},
}
STORE_MANIFEST = "MANIFEST.json"  # the whole-store manifest's name, in the store and manifests dir as STORE.json


def store_manifest(entries: dict, cfg: dict) -> mf.TreeManifest:
    """One manifest for the whole store, composed from the per-entry manifests."""
    files, sources = [], {}
    for name, spec in sorted(entries.items()):
        m = mf.load_tree(REPO / cfg["manifests"] / f"{name}.json")
        for f in m.files:
            files.append(mf.FileEntry(f"{spec['local_dir']}/{f.path}", f.size, f.sha256, f.upstream))
        sources[name] = {"local_dir": spec["local_dir"], "tree_hash": m.tree_hash, **m.source}
    from datetime import datetime, timezone

    return mf.TreeManifest(kind="store", name="data-store", files=sorted(files, key=lambda f: f.path),
                           source={"entries": sources},
                           created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def verify_store(m: mf.TreeManifest, store: Path) -> list[str]:
    """Problems with the store against its manifest; the in-store manifest file is exempt."""
    problems = mf.verify_tree(m, store)
    return [p for p in problems if p != f"unexpected: {STORE_MANIFEST}"]


def write_store_manifest(entries: dict, cfg: dict, store: Path) -> None:
    m = store_manifest(entries, cfg)
    committed = REPO / cfg["manifests"] / "STORE.json"
    if committed.exists():
        old = mf.load_tree(committed)
        if old.tree_hash == m.tree_hash:
            m = old  # unchanged contents: keep the original created_at
    problems = verify_store(m, store)
    if problems:
        raise FetchError(f"store does not match its manifest: {problems[:5]}")
    m.save(committed)
    dest = store / STORE_MANIFEST
    if dest.exists():
        dest.chmod(0o644)
    m.save(dest)
    dest.chmod(0o444)
    log(f"store manifest: {len(m.files)} files, {m.total_bytes / 1e9:.1f} GB, tree {m.tree_hash[:16]} "
        f"-> {committed.relative_to(REPO)} and {dest.name}")


def log(msg: str) -> None:
    print(f"[fetch] {msg}", flush=True)


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- stamps


def _stamp_path(state_dir: Path, name: str, role: str) -> Path:
    # name arrives as "<prefix>-<entry name>"
    return state_dir / "stamps" / f"{name}-{role}.json"


def _file_stats(root: Path, paths: list[str]) -> dict[str, list[int]]:
    out = {}
    for p in paths:
        st = (root / p).stat()
        out[p] = [st.st_size, st.st_mtime_ns]
    return out


def stamp_valid(state_dir: Path, name: str, role: str, root: Path, m: mf.TreeManifest) -> bool:
    sp = _stamp_path(state_dir, name, role)
    if not sp.exists() or not root.is_dir():
        return False
    s = json.loads(sp.read_text())
    if s.get("tree_hash") != m.tree_hash or s.get("root") != str(root.resolve()):
        return False
    try:
        paths = mf.list_tree(root)
    except mf.ManifestError:
        return False
    if paths != sorted(f.path for f in m.files):
        return False
    return _file_stats(root, paths) == s.get("files")


def write_stamp(state_dir: Path, name: str, role: str, root: Path, m: mf.TreeManifest) -> None:
    sp = _stamp_path(state_dir, name, role)
    sp.parent.mkdir(parents=True, exist_ok=True)
    paths = [f.path for f in m.files]
    sp.write_text(json.dumps({"tree_hash": m.tree_hash, "root": str(root.resolve()),
                              "files": _file_stats(root, paths)}, indent=1) + "\n")


def make_read_only(root: Path, paths: list[str]) -> None:
    for p in paths:
        f = root / p
        mode = f.stat().st_mode
        f.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


# ---------------------------------------------------------------- upstream


def git_blob_sha1(path: Path) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % path.stat().st_size)
    with open(path, "rb") as f:
        while chunk := f.read(16 * 2**20):
            h.update(chunk)
    return h.hexdigest()


def _selected(path: str, patterns: list[str] | None) -> bool:
    return patterns is None or any(fnmatch.fnmatch(path, p) for p in patterns)


def upstream_files(repo_id: str, revision: str, repo_type: str = "model",
                   patterns: list[str] | None = None) -> dict[str, dict]:
    from huggingface_hub import HfApi

    api = HfApi()
    getter = api.dataset_info if repo_type == "dataset" else api.model_info
    info = getter(repo_id, revision=revision, files_metadata=True)
    if info.sha != revision:
        raise FetchError(f"{repo_id}: hub returned revision {info.sha}, pinned {revision}")
    out = {}
    for s in info.siblings:
        if not _selected(s.rfilename, patterns):
            continue
        lfs = s.lfs.sha256 if s.lfs is not None else None
        out[s.rfilename] = {"size": s.size, "blob_id": s.blob_id, "lfs_sha256": lfs}
    return out


def download(spec: dict, dest: Path) -> None:
    from huggingface_hub import snapshot_download

    log(f"downloading {spec['repo_id']}@{spec['revision'][:12]} -> {dest}")
    snapshot_download(repo_id=spec["repo_id"], revision=spec["revision"], local_dir=str(dest),
                      repo_type=spec.get("repo_type", "model"),
                      allow_patterns=spec.get("allow_patterns"), max_workers=8)


def first_manifest(name: str, spec: dict, root: Path, kind: str) -> mf.TreeManifest:
    """Hash the fresh download and confirm every file against the publisher's digests."""
    up = upstream_files(spec["repo_id"], spec["revision"], spec.get("repo_type", "model"),
                        spec.get("allow_patterns"))
    log(f"{name}: hashing {len(up)} files and checking them against upstream digests")
    source = {"repo_id": spec["repo_id"], "revision": spec["revision"]}
    if spec.get("repo_type", "model") != "model":
        source["repo_type"] = spec["repo_type"]
    if spec.get("allow_patterns"):
        source["allow_patterns"] = spec["allow_patterns"]
    m = mf.build_tree(root, kind, name, local_dir_name=spec["local_dir"], source=source)
    have = {f.path for f in m.files}
    if have != set(up):
        raise FetchError(f"{name}: local files differ from upstream: "
                         f"missing {sorted(set(up) - have)}, extra {sorted(have - set(up))}")
    for f in m.files:
        u = up[f.path]
        if u["size"] is not None and u["size"] != f.size:
            raise FetchError(f"{name}: {f.path} size {f.size} != upstream {u['size']}")
        if u["lfs_sha256"]:
            if u["lfs_sha256"] != f.sha256:
                raise FetchError(f"{name}: {f.path} sha256 differs from upstream LFS digest")
            f.upstream = "hf-lfs-sha256"
        elif u["blob_id"]:
            if git_blob_sha1(root / f.path) != u["blob_id"]:
                raise FetchError(f"{name}: {f.path} differs from upstream git blob")
            f.upstream = "hf-git-blob-sha1"
    return m


# ---------------------------------------------------------------- stages


def stage_store(name: str, spec: dict, store: Path, state_dir: Path, rehash: bool, cfg: dict) -> None:
    root = store / spec["local_dir"]
    mpath = REPO / cfg["manifests"] / f"{name}.json"
    sname = f"{cfg['stamp']}-{name}"

    if mpath.exists():
        m = mf.load_tree(mpath)
        if m.source.get("revision") != spec["revision"] or m.source.get("repo_id") != spec["repo_id"]:
            raise FetchError(f"{name}: manifest pins {m.source}, config pins "
                             f"{spec['repo_id']}@{spec['revision']}; a new revision needs a new manifest")
        if not rehash and stamp_valid(state_dir, sname, "store", root, m):
            log(f"{name}: store verified earlier, files unchanged -> skip")
            return
        want = {f.path: f.size for f in m.files}
        present = root.is_dir() and all((root / p).is_file() and (root / p).stat().st_size == s
                                        for p, s in want.items())
        if not present:
            download(spec, root)
        log(f"{name}: full sha256 check of store against {mpath.relative_to(REPO)}")
        mf.require_tree(m, root)
    else:
        download(spec, root)
        m = first_manifest(name, spec, root, cfg["kind"])
        m.save(mpath)
        log(f"{name}: wrote {mpath.relative_to(REPO)} (tree {m.tree_hash[:16]}, {m.total_bytes / 1e9:.1f} GB)")

    make_read_only(root, [f.path for f in m.files])
    write_stamp(state_dir, sname, "store", root, m)
    log(f"{name}: store OK (tree {m.tree_hash[:16]})")


def stage_cache(name: str, spec: dict, store: Path, cache: Path | None, state_dir: Path,
                rehash: bool, cfg: dict) -> None:
    if cfg["cache"] is None:
        return
    if cache is None:
        log(f"{name}: {cfg['cache']} unset -> skip fast-load copy")
        return
    m = mf.load_tree(REPO / cfg["manifests"] / f"{name}.json")
    sname = f"{cfg['stamp']}-{name}"
    src, dst = store / spec["local_dir"], cache / spec["local_dir"]
    if not rehash and stamp_valid(state_dir, sname, "cache", dst, m):
        log(f"{name}: cache verified earlier, files unchanged -> skip")
        return
    if not cache.is_dir() or not os.access(cache, os.W_OK):
        raise FetchError(f"cache root {cache} does not exist or is not writable")
    log(f"{name}: copying {src} -> {dst}")
    dst.mkdir(exist_ok=True)
    # --chmod u+w during transfer so a re-run can replace a damaged partial copy.
    subprocess.run(["rsync", "-a", "--delete", "--exclude", ".cache/", "--chmod", "u+w",
                    f"{src}/", f"{dst}/"], check=True)
    log(f"{name}: full sha256 check of cache copy")
    mf.require_tree(m, dst)
    make_read_only(dst, [f.path for f in m.files])
    write_stamp(state_dir, sname, "cache", dst, m)
    log(f"{name}: cache OK")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", dest="set_", choices=sorted(SETS), default="models")
    ap.add_argument("--name", "--model", dest="names", action="append", help="only this entry (repeatable)")
    ap.add_argument("--rehash", action="store_true", help="ignore stamps; full sha256 check")
    ap.add_argument("--skip-cache", action="store_true")
    a = ap.parse_args(argv)

    cfg = SETS[a.set_]
    store_env = os.environ.get(cfg["store"])
    if not store_env:
        log(f"{cfg['store']} is not set (see .env.example)")
        return 2
    store = Path(store_env)
    if not store.is_dir():
        log(f"{cfg['store']}={store} is not a directory")
        return 2
    cache_env = (os.environ.get(cfg["cache"]) or None) if cfg["cache"] else None
    cache = None if a.skip_cache or not cache_env else Path(cache_env)
    state_dir = Path(os.environ.get("WEAVER_TRAIN_STATE_DIR") or REPO / "var")
    if not state_dir.is_absolute():
        state_dir = REPO / state_dir

    entries = tomllib.loads((REPO / cfg["config"]).read_text())[a.set_]
    names = a.names or list(entries)
    try:
        for name in names:
            if name not in entries:
                raise FetchError(f"unknown {a.set_} entry {name!r}; known: {sorted(entries)}")
            stage_store(name, entries[name], store, state_dir, a.rehash, cfg)
            stage_cache(name, entries[name], store, cache, state_dir, a.rehash, cfg)
        if a.set_ == "datasets":
            write_store_manifest(entries, cfg, store)
    except (FetchError, mf.ManifestError, subprocess.CalledProcessError) as e:
        log(f"FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
