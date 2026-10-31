"""Shared record formats for weaver-train. The only module other modules may import.

Two formats live here:

* **Tree manifest** -- the sha256 of every file under a directory (a model's weights,
  a benchmark snapshot). Its ``tree_hash`` is the sha256 of the sorted
  ``"<sha256>  <path>\\n"`` lines, so ``sha256sum``-style tooling can reproduce it.
  Model manifests also carry the upstream repo id and pinned revision.
* **Run record** -- one per training run or benchmark repetition: model hash,
  snapshot hash, seed, sampler config, precision, commit, engine. ``engine`` is always
  ``"python-hf"``; these records form a separate tuple from any WeaverTools run.

Both are JSON. Neither embeds absolute paths, since manifests are committed to a
public repository; files are listed relative to the tree root.

CLI::

    python -m weaver_train.manifest build ROOT --kind model --name NAME [--out FILE]
    python -m weaver_train.manifest verify MANIFEST ROOT
    python -m weaver_train.manifest template [--out FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ENGINE = "python-hf"
TREE_SCHEMA = "weaver-train/tree-manifest/v1"
RUN_SCHEMA = "weaver-train/run-record/v1"
PRECISIONS = ("bf16",)
RUN_KINDS = ("train", "bench")

# Never part of a tree: hf download bookkeeping and editor/OS litter.
IGNORED_DIRS = {".cache", ".git", "__pycache__"}
IGNORED_FILES = {".DS_Store"}

_CHUNK = 16 * 2**20


class ManifestError(Exception):
    """A manifest is malformed, or a tree does not match it."""


# --------------------------------------------------------------------------- hashing


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def list_tree(root: str | os.PathLike) -> list[str]:
    """Relative POSIX paths of every regular file under root, sorted."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for d in dirnames:  # os.walk lists a linked directory here and does not descend
            if Path(dirpath, d).is_symlink():
                raise ManifestError(f"symlink in tree, refusing to follow: {Path(dirpath, d).relative_to(root)}")
        for name in filenames:
            if name in IGNORED_FILES:
                continue
            p = Path(dirpath, name)
            if p.is_symlink():
                raise ManifestError(f"symlink in tree, refusing to follow: {p.relative_to(root)}")
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def hash_files(root: str | os.PathLike, paths: list[str], workers: int = 8) -> dict[str, str]:
    root = Path(root)
    with ThreadPoolExecutor(max_workers=workers) as ex:  # hashlib releases the GIL
        digests = ex.map(lambda p: sha256_file(root / p), paths)
        return dict(zip(paths, digests))


def tree_hash_of(files: dict[str, str]) -> str:
    lines = "".join(f"{digest}  {path}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


# ------------------------------------------------------------------- tree manifests


@dataclass
class FileEntry:
    path: str
    size: int
    sha256: str
    # How the digest was confirmed against the publisher, if at all:
    # "hf-lfs-sha256", "hf-git-blob-sha1", or None (locally computed only).
    upstream: str | None = None


@dataclass
class TreeManifest:
    kind: str  # "model" | "snapshot"
    name: str
    files: list[FileEntry]
    source: dict = field(default_factory=dict)  # e.g. {"repo_id": ..., "revision": ...}
    local_dir_name: str | None = None  # directory name under a store root
    schema: str = TREE_SCHEMA
    created_at: str | None = None

    @property
    def tree_hash(self) -> str:
        return tree_hash_of({f.path: f.sha256 for f in self.files})

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def to_json(self) -> str:
        d = asdict(self)
        d["tree_hash"] = self.tree_hash
        d["total_bytes"] = self.total_bytes
        return json.dumps(d, indent=2) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "TreeManifest":
        if d.get("schema") != TREE_SCHEMA:
            raise ManifestError(f"unknown tree manifest schema {d.get('schema')!r}")
        m = cls(
            kind=d["kind"], name=d["name"], files=[FileEntry(**f) for f in d["files"]],
            source=d.get("source", {}), local_dir_name=d.get("local_dir_name"),
            created_at=d.get("created_at"),
        )
        if "tree_hash" in d and d["tree_hash"] != m.tree_hash:
            raise ManifestError(f"{m.name}: stored tree_hash does not match its file list")
        return m

    def save(self, path: str | os.PathLike) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json())


def load_tree(path: str | os.PathLike) -> TreeManifest:
    return TreeManifest.from_dict(json.loads(Path(path).read_text()))


def build_tree(root: str | os.PathLike, kind: str, name: str, source: dict | None = None,
               local_dir_name: str | None = None, workers: int = 8) -> TreeManifest:
    root = Path(root)
    paths = list_tree(root)
    if not paths:
        raise ManifestError(f"no files under {root}")
    digests = hash_files(root, paths, workers)
    files = [FileEntry(p, (root / p).stat().st_size, digests[p]) for p in paths]
    return TreeManifest(kind=kind, name=name, files=files, source=source or {},
                        local_dir_name=local_dir_name,
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def verify_tree(manifest: TreeManifest, root: str | os.PathLike, workers: int = 8,
                paths: list[str] | None = None) -> list[str]:
    """Full sha256 check of root against manifest. Returns problems; empty means match.

    Extra files under root count as problems: a tree must be exactly its manifest.
    ``paths`` limits hashing to those entries (sizes and the file set are always checked).
    """
    root = Path(root)
    if not root.is_dir():
        return [f"not a directory: {root}"]
    want = {f.path: f for f in manifest.files}
    have = set(list_tree(root))
    problems = [f"missing: {p}" for p in sorted(want.keys() - have)]
    problems += [f"unexpected: {p}" for p in sorted(have - want.keys())]
    present = sorted(want.keys() & have)
    sized = []
    for p in present:
        size = (root / p).stat().st_size
        if size != want[p].size:
            problems.append(f"size mismatch: {p} ({size} != {want[p].size})")
        else:
            sized.append(p)
    to_hash = sized if paths is None else [p for p in sized if p in set(paths)]
    for p, digest in hash_files(root, to_hash, workers).items():
        if digest != want[p].sha256:
            problems.append(f"sha256 mismatch: {p}")
    return problems


def require_tree(manifest: TreeManifest, root: str | os.PathLike, workers: int = 8) -> str:
    """verify_tree, raising ManifestError on any problem. Returns the tree hash."""
    problems = verify_tree(manifest, root, workers)
    if problems:
        shown = "; ".join(problems[:10]) + (f"; ... {len(problems) - 10} more" if len(problems) > 10 else "")
        raise ManifestError(f"{manifest.name}: tree does not match manifest: {shown}")
    return manifest.tree_hash


# ---------------------------------------------------------------------- run records


def git_commit(repo_dir: str | os.PathLike | None = None) -> dict:
    """Current commit of the weaver-train checkout and whether the tree is dirty."""
    cwd = repo_dir or Path(__file__).resolve().parents[2]
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True,
                                    text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return {"rev": None, "dirty": None}
    return {"rev": rev, "dirty": dirty}


@dataclass
class RunRecord:
    kind: str  # "train" | "bench"
    model: dict  # {"name", "tree_hash"}
    seed: int
    precision: str = "bf16"
    sampler: dict = field(default_factory=dict)  # temperature, top_p, top_k, max_new_tokens, ...
    snapshot: dict | None = None  # {"name", "tree_hash"} for bench runs
    adapter: dict | None = None  # {"name", "tree_hash"} when an adapter is applied/produced
    dataset: dict | None = None  # {"file_name", "sha256"} for train runs
    config: dict = field(default_factory=dict)  # trainer or bench config, verbatim
    repetition: int | None = None  # 0-based index for repeated bench runs
    commit: dict = field(default_factory=git_commit)
    engine: str = ENGINE
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    status: str = "started"  # "started" | "completed" | "failed"
    results: dict = field(default_factory=dict)
    schema: str = RUN_SCHEMA

    def validate(self) -> None:
        if self.engine != ENGINE:
            raise ManifestError(f"engine must be {ENGINE!r}, got {self.engine!r}")
        if self.kind not in RUN_KINDS:
            raise ManifestError(f"kind must be one of {RUN_KINDS}, got {self.kind!r}")
        if self.precision not in PRECISIONS:
            raise ManifestError(f"precision must be one of {PRECISIONS}, got {self.precision!r}")
        if not isinstance(self.model, dict) or not self.model.get("tree_hash"):
            raise ManifestError("model.tree_hash is required")
        if self.kind == "bench" and not (self.snapshot or {}).get("tree_hash"):
            raise ManifestError("bench runs require snapshot.tree_hash")
        if not isinstance(self.seed, int):
            raise ManifestError("seed must be an int")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def save(self, path: str | os.PathLike) -> None:
        text = self.to_json()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{path}.tmp")
        tmp.write_text(text)
        tmp.replace(path)


def load_run(path: str | os.PathLike) -> RunRecord:
    d = json.loads(Path(path).read_text())
    if d.get("schema") != RUN_SCHEMA:
        raise ManifestError(f"unknown run record schema {d.get('schema')!r}")
    rec = RunRecord(**d)
    rec.validate()
    return rec


def run_template() -> dict:
    """A filled-in example run record, for documentation and as a starting config."""
    rec = RunRecord(
        kind="bench",
        model={"name": "<model name>", "tree_hash": "<sha256 from manifests/models/<name>.json>"},
        snapshot={"name": "<snapshot name>", "tree_hash": "<sha256 of the snapshot manifest>"},
        seed=0,
        sampler={"temperature": 0.0, "top_p": 1.0, "top_k": 0, "max_new_tokens": 4096},
        repetition=0,
        commit={"rev": "<git rev>", "dirty": False},
        run_id="<generated>",
        created_at="<generated>",
    )
    return asdict(rec)


# ------------------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m weaver_train.manifest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="hash a tree and write its manifest")
    b.add_argument("root")
    b.add_argument("--kind", required=True, choices=["model", "snapshot"])
    b.add_argument("--name", required=True)
    b.add_argument("--out")
    v = sub.add_parser("verify", help="full sha256 check of a tree against a manifest")
    v.add_argument("manifest")
    v.add_argument("root")
    t = sub.add_parser("template", help="print an example run record")
    t.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        m = build_tree(a.root, a.kind, a.name, local_dir_name=Path(a.root).resolve().name)
        (Path(a.out).write_text(m.to_json()) if a.out else sys.stdout.write(m.to_json()))
        return 0
    if a.cmd == "verify":
        problems = verify_tree(load_tree(a.manifest), a.root)
        for p in problems:
            print(p, file=sys.stderr)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 0 if not problems else 1
    text = json.dumps(run_template(), indent=2, sort_keys=True) + "\n"
    (Path(a.out).write_text(text) if a.out else sys.stdout.write(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
