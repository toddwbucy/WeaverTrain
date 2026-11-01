"""Freeze dated benchmark snapshots from the pinned sources in config/benchmarks.toml.

    python scripts/build_snapshot.py [--name NAME] [--rehash]

Each [snapshots.<name>] in config/snapshots.toml names a source, the source files to
read, an optional created_at floor, and a freeze date. The snapshot is written to
``$WEAVER_TRAIN_SNAPSHOT_STORE/<name>/`` as:

  tasks.jsonl    one task per line, sorted by task_id, keys sorted: the bench input
  SNAPSHOT.json  provenance: source repo, revision and tree hash, filter, counts, caveats

and hashed into manifests/snapshots/<name>.json (kind "snapshot", the manifest
weaver_train.bench verifies). The build is deterministic: the same pinned source
always produces the same tree hash. So:

  * no manifest yet           -> build, write manifest, make read-only
  * manifest, stamp valid     -> skip
  * manifest, no valid stamp  -> rebuild in a scratch dir and require the identical
                                 tree hash, then verify the stored copy against it

A source whose own manifest does not verify refuses the build.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from weaver_train import manifest as mf  # noqa: E402
from fetch_artifacts import make_read_only, stamp_valid, write_stamp  # noqa: E402


def log(msg: str) -> None:
    print(f"[snapshot] {msg}", flush=True)


class SnapshotError(Exception):
    pass


def read_rows(path: Path) -> list[dict]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    raise SnapshotError(f"unsupported source file type: {path.name}")


def collect(spec: dict, src_root: Path, src_manifest: mf.TreeManifest) -> tuple[list[dict], dict]:
    files = sorted(f.path for f in src_manifest.files
                   if any(fnmatch.fnmatch(f.path, p) for p in spec["files"]))
    if not files:
        raise SnapshotError(f"no source files match {spec['files']}")
    id_field = spec.get("id_field", "instance_id")
    date_field = spec.get("date_field")
    floor = spec.get("created_at_min")
    tasks: dict[str, dict] = {}
    seen = dropped_date = 0
    for rel in files:
        for row in read_rows(src_root / rel):
            seen += 1
            tid = row.get(id_field)
            if not isinstance(tid, str) or not tid:
                raise SnapshotError(f"{rel}: row without {id_field}")
            if floor is not None:
                if str(row.get(date_field) or "") < floor:
                    dropped_date += 1
                    continue
            row = {**row, "task_id": tid, "source_file": rel}
            if tid in tasks:
                if json.dumps(tasks[tid], sort_keys=True, default=str) != json.dumps(row, sort_keys=True, default=str):
                    # identical duplicates (the same task in two split files) are merged
                    if {k: v for k, v in tasks[tid].items() if k != "source_file"} != \
                       {k: v for k, v in row.items() if k != "source_file"}:
                        raise SnapshotError(f"task {tid} appears twice with different content")
                continue
            tasks[tid] = row
    if not tasks:
        raise SnapshotError(f"selection is empty: {seen} rows read, {dropped_date} dropped by date "
                            f"(date_field={date_field!r}, created_at_min={floor!r})")
    stats = {"rows_read": seen, "dropped_by_date": dropped_date, "tasks": len(tasks), "files": files}
    if date_field and tasks:
        dates = sorted(str(t.get(date_field)) for t in tasks.values())
        stats["created_at_range"] = [dates[0], dates[-1]]
    return [tasks[k] for k in sorted(tasks)], stats


def write_snapshot(dest: Path, name: str, spec: dict, src_name: str, src_spec: dict,
                   src_manifest: mf.TreeManifest, tasks: list[dict], stats: dict) -> None:
    dest.mkdir(parents=True)
    with open(dest / "tasks.jsonl", "w") as f:
        for t in tasks:
            f.write(json.dumps(t, sort_keys=True, default=str, ensure_ascii=False) + "\n")
    prov = {
        "name": name,
        "frozen_on": spec["frozen_on"],
        "description": spec.get("description", ""),
        "source": {"name": src_name, "repo_id": src_spec["repo_id"], "revision": src_spec["revision"],
                   "tree_hash": src_manifest.tree_hash},
        "filter": {k: spec[k] for k in ("files", "date_field", "created_at_min") if k in spec},
        "counts": {k: v for k, v in stats.items() if k != "files"},
        "caveats": spec.get("caveats", []),
    }
    (dest / "SNAPSHOT.json").write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n")


def build_one(name: str, spec: dict, sources: dict, state_dir: Path, rehash: bool) -> None:
    src_name = spec["source"]
    src_spec = sources[src_name]
    src_manifest = mf.load_tree(REPO / "manifests" / "sources" / f"{src_name}.json")
    src_root = Path(os.environ["WEAVER_TRAIN_BENCH_STORE"]) / src_spec["local_dir"]
    store = Path(os.environ["WEAVER_TRAIN_SNAPSHOT_STORE"])
    dest = store / name
    mpath = REPO / "manifests" / "snapshots" / f"{name}.json"
    sname = f"snapshot-{name}"

    if mpath.exists():
        m = mf.load_tree(mpath)
        if not rehash and stamp_valid(state_dir, sname, "store", dest, m):
            log(f"{name}: verified earlier, files unchanged -> skip")
            return

    mf.require_tree(src_manifest, src_root)  # the source must be intact before deriving from it
    tasks, stats = collect(spec, src_root, src_manifest)
    with tempfile.TemporaryDirectory(dir=store) as tmp:
        built = Path(tmp) / name
        write_snapshot(built, name, spec, src_name, src_spec, src_manifest, tasks, stats)
        fresh = mf.build_tree(built, "snapshot", name, local_dir_name=name,
                              source={"from": src_name, "repo_id": src_spec["repo_id"],
                                      "revision": src_spec["revision"]})
        if mpath.exists():
            m = mf.load_tree(mpath)
            if fresh.tree_hash != m.tree_hash:
                raise SnapshotError(f"{name}: rebuild gives tree {fresh.tree_hash[:16]}, manifest pins "
                                    f"{m.tree_hash[:16]}; the build is not reproducing the frozen snapshot")
            if not dest.exists():
                shutil.move(str(built), dest)
            log(f"{name}: rebuild reproduces the manifest; verifying stored copy")
            mf.require_tree(m, dest)
        else:
            if dest.exists():
                raise SnapshotError(f"{dest} exists without a manifest; move it aside first")
            shutil.move(str(built), dest)
            fresh.save(mpath)
            m = fresh
            log(f"{name}: froze {stats['tasks']} tasks -> {mpath.relative_to(REPO)} (tree {m.tree_hash[:16]})")
    make_read_only(dest, [f.path for f in m.files])
    write_stamp(state_dir, sname, "store", dest, m)
    log(f"{name}: OK ({stats['tasks']} tasks, created_at {stats.get('created_at_range', 'n/a')})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", action="append")
    ap.add_argument("--rehash", action="store_true")
    a = ap.parse_args(argv)
    for var in ("WEAVER_TRAIN_BENCH_STORE", "WEAVER_TRAIN_SNAPSHOT_STORE"):
        if not os.environ.get(var):
            log(f"{var} is not set (see .env.example)")
            return 2
    state_dir = Path(os.environ.get("WEAVER_TRAIN_STATE_DIR") or REPO / "var")
    state_dir = state_dir if state_dir.is_absolute() else REPO / state_dir
    sources = tomllib.loads((REPO / "config" / "benchmarks.toml").read_text())["benchmarks"]
    snaps = tomllib.loads((REPO / "config" / "snapshots.toml").read_text())["snapshots"]
    try:
        for name in a.name or list(snaps):
            if name not in snaps:
                raise SnapshotError(f"unknown snapshot {name!r}; known {sorted(snaps)}")
            build_one(name, snaps[name], sources, state_dir, a.rehash)
    except (SnapshotError, mf.ManifestError) as e:
        log(f"FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
