"""Fetch, verify and stage the reference models in config/models.toml.

Two stages per model, each skipped when already done:

store  Download the pinned revision into $WEAVER_TRAIN_MODEL_STORE/<local_dir>.
       First time: hash every file and check it against the publisher's own digest
       (LFS sha256, or git blob sha1 for small files), then write
       manifests/models/<name>.json. Afterwards: full sha256 check against that
       manifest. Files are then made read-only.
cache  Copy the verified tree to $WEAVER_TRAIN_MODEL_CACHE/<local_dir> (the fast-load
       copy the loader reads), full sha256 check, read-only. Skipped if unset.

"Already done" is a stamp in $WEAVER_TRAIN_STATE_DIR/stamps recording the verified
tree hash plus each file's size and mtime. A matching stamp skips re-hashing; any
size/mtime change, missing or extra file voids it and forces a full check.
--rehash ignores stamps.

A tree that is complete but fails its manifest is never repaired automatically:
the script stops and reports, because a reference artifact has changed.
"""

from __future__ import annotations

import argparse
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


def log(msg: str) -> None:
    print(f"[fetch_models] {msg}", flush=True)


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- stamps


def _stamp_path(state_dir: Path, name: str, role: str) -> Path:
    return state_dir / "stamps" / f"model-{name}-{role}.json"


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


def upstream_files(repo_id: str, revision: str) -> dict[str, dict]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    if info.sha != revision:
        raise FetchError(f"{repo_id}: hub returned revision {info.sha}, pinned {revision}")
    out = {}
    for s in info.siblings:
        lfs = s.lfs.sha256 if s.lfs is not None else None
        out[s.rfilename] = {"size": s.size, "blob_id": s.blob_id, "lfs_sha256": lfs}
    return out


def download(repo_id: str, revision: str, dest: Path) -> None:
    from huggingface_hub import snapshot_download

    log(f"downloading {repo_id}@{revision[:12]} -> {dest}")
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=str(dest), max_workers=8)


def first_manifest(name: str, spec: dict, root: Path) -> mf.TreeManifest:
    """Hash the fresh download and confirm every file against the publisher's digests."""
    up = upstream_files(spec["repo_id"], spec["revision"])
    log(f"{name}: hashing {len(up)} files and checking them against upstream digests")
    m = mf.build_tree(root, "model", name, local_dir_name=spec["local_dir"],
                      source={"repo_id": spec["repo_id"], "revision": spec["revision"]})
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


def stage_store(name: str, spec: dict, store: Path, state_dir: Path, rehash: bool) -> None:
    root = store / spec["local_dir"]
    mpath = REPO / "manifests" / "models" / f"{name}.json"

    if mpath.exists():
        m = mf.load_tree(mpath)
        if m.source.get("revision") != spec["revision"] or m.source.get("repo_id") != spec["repo_id"]:
            raise FetchError(f"{name}: manifest pins {m.source}, config pins "
                             f"{spec['repo_id']}@{spec['revision']}; a new revision needs a new manifest")
        if not rehash and stamp_valid(state_dir, name, "store", root, m):
            log(f"{name}: store verified earlier, files unchanged -> skip")
            return
        want = {f.path: f.size for f in m.files}
        present = root.is_dir() and all((root / p).is_file() and (root / p).stat().st_size == s
                                        for p, s in want.items())
        if not present:
            download(spec["repo_id"], spec["revision"], root)
        log(f"{name}: full sha256 check of store against {mpath.relative_to(REPO)}")
        mf.require_tree(m, root)
    else:
        download(spec["repo_id"], spec["revision"], root)
        m = first_manifest(name, spec, root)
        m.save(mpath)
        log(f"{name}: wrote {mpath.relative_to(REPO)} (tree {m.tree_hash[:16]}, {m.total_bytes / 1e9:.1f} GB)")

    make_read_only(root, [f.path for f in m.files])
    write_stamp(state_dir, name, "store", root, m)
    log(f"{name}: store OK (tree {m.tree_hash[:16]})")


def stage_cache(name: str, spec: dict, store: Path, cache: Path | None, state_dir: Path,
                rehash: bool) -> None:
    if cache is None:
        log(f"{name}: WEAVER_TRAIN_MODEL_CACHE unset -> skip fast-load copy")
        return
    m = mf.load_tree(REPO / "manifests" / "models" / f"{name}.json")
    src, dst = store / spec["local_dir"], cache / spec["local_dir"]
    if not rehash and stamp_valid(state_dir, name, "cache", dst, m):
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
    write_stamp(state_dir, name, "cache", dst, m)
    log(f"{name}: cache OK")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", action="append", help="only this model (repeatable)")
    ap.add_argument("--config", default=str(REPO / "config" / "models.toml"))
    ap.add_argument("--rehash", action="store_true", help="ignore stamps; full sha256 check")
    ap.add_argument("--skip-cache", action="store_true")
    a = ap.parse_args(argv)

    store_env = os.environ.get("WEAVER_TRAIN_MODEL_STORE")
    if not store_env:
        log("WEAVER_TRAIN_MODEL_STORE is not set (see .env.example)")
        return 2
    store = Path(store_env)
    cache_env = os.environ.get("WEAVER_TRAIN_MODEL_CACHE") or None
    cache = None if a.skip_cache or not cache_env else Path(cache_env)
    state_dir = Path(os.environ.get("WEAVER_TRAIN_STATE_DIR") or REPO / "var")
    if not state_dir.is_absolute():
        state_dir = REPO / state_dir

    models = tomllib.loads(Path(a.config).read_text())["models"]
    names = a.model or list(models)
    try:
        for name in names:
            if name not in models:
                raise FetchError(f"unknown model {name!r}; known: {sorted(models)}")
            stage_store(name, models[name], store, state_dir, a.rehash)
            stage_cache(name, models[name], store, cache, state_dir, a.rehash)
    except (FetchError, mf.ManifestError, subprocess.CalledProcessError) as e:
        log(f"FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
