"""Find benchmark tasks inside a candidate training set, by exact keys.

    python scripts/contamination.py --snapshot NAME --dataset NAME [--out DIR]

The snapshot is a frozen benchmark snapshot (config/snapshots.toml), verified against
its manifest first. The dataset is an entry in config/datasets.toml, verified against
its manifest first. Every training example is scanned once.

A task matches an example on any of these exact keys, each recorded separately:

  issue_text   the task's problem statement, whitespace-normalized, found inside a
               user message of the example (whole statement, or its first 400
               characters when longer)
  base_commit  the task's 40-hex base commit anywhere in the example
  instance_id  the task's instance id (owner__repo-N) anywhere in the example
  github_ref   github.com/<repo>/(pull|issues)/<N> for the task's PR number or any of
               its issue numbers (Multi-SWE-bench records the resolved issues)

Both snapshot schemas are read: SWE-bench style (repo "owner/name", base_commit,
problem_statement) and Multi-SWE-bench (org + repo, base.sha, resolved_issues with
their numbers and bodies; the issue bodies are the statement).

Repo overlap (same owner/repo, case-insensitive) is counted but is not a match: it
says where a match could be, not that the task is there.

Writes <out>/<dataset>--<snapshot>.json: counts, every hit (task, example uuid, keys)
and ``drop``, the uuids of examples to exclude from training. Read-only on both trees.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from weaver_train import manifest as mf  # noqa: E402

ISSUE_PREFIX = 400
WS = re.compile(r"\s+")
HEX40 = re.compile(r"\b[0-9a-f]{40}\b")
# Anchored at a token start: unanchored, the regex retries from every character of a
# long word run and is ~30x slower on this data (same matches).
INSTANCE = re.compile(r"(?<![\w.-])[\w.-]+__[\w.-]+-\d+")
GHREF = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/(?:pull|issues)/(\d+)", re.I)


def norm(text: str) -> str:
    return WS.sub(" ", text).strip()


def load_snapshot(name: str) -> tuple[list[dict], str]:
    mpath = REPO / "manifests" / "snapshots" / f"{name}.json"
    m = mf.load_tree(mpath)
    root = Path(os.environ["WEAVER_TRAIN_SNAPSHOT_STORE"]) / name
    mf.require_tree(m, root)
    tasks = [json.loads(line) for line in (root / "tasks.jsonl").read_text().splitlines() if line.strip()]
    return tasks, m.tree_hash


def normalize(t: dict) -> dict:
    """One shape for both snapshot schemas."""
    if "org" in t:  # Multi-SWE-bench
        issues = t.get("resolved_issues") or []
        return {"instance_id": t["instance_id"], "repo": f"{t['org']}/{t['repo']}",
                "number": str(t["number"]), "base_commit": (t.get("base") or {}).get("sha"),
                "statements": [i.get("body") or "" for i in issues],
                "issue_numbers": [str(i["number"]) for i in issues if i.get("number") is not None]}
    return {"instance_id": t["instance_id"], "repo": t["repo"],
            "number": t["instance_id"].rsplit("-", 1)[-1], "base_commit": t.get("base_commit"),
            "statements": [t.get("problem_statement") or ""], "issue_numbers": []}


def dataset_files(name: str) -> tuple[Path, list[str], str]:
    spec = tomllib.loads((REPO / "config" / "datasets.toml").read_text())["datasets"][name]
    m = mf.load_tree(REPO / "manifests" / "datasets" / f"{name}.json")
    root = Path(os.environ["WEAVER_TRAIN_DATA_STORE"]) / spec["local_dir"]
    mf.require_tree(m, root)
    data = [f.path for f in m.files if f.path.endswith((".jsonl", ".parquet"))]
    return root, data, m.tree_hash


def examples(root: Path, files: list[str]):
    """Yield (uuid, repo or None, user texts, full text) per example."""
    for rel in files:
        path = root / rel
        if path.suffix == ".jsonl":
            with open(path) as f:
                for line in f:
                    yield _example(json.loads(line), line)
        else:
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(path)
            for i in range(pf.num_row_groups):
                for row in pf.read_row_group(i).to_pylist():
                    yield _example(row, json.dumps(row))


def _example(row: dict, raw: str):
    msgs = row.get("messages") or []
    users = [m.get("content") or "" for m in msgs if m.get("role") in ("user", "human")]
    users = [u if isinstance(u, str) else json.dumps(u) for u in users]
    uuid = row.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        raise ValueError("example without a uuid: a hit on it could not be dropped from training")
    return uuid, row.get("repo"), users, raw


def match(raw_tasks: list[dict], rows) -> dict:
    """Match tasks against an iterable of (uuid, repo, user texts, raw text)."""
    tasks = [normalize(t) for t in raw_tasks]
    by_repo = defaultdict(list)
    for t in tasks:
        for stmt in (norm(x) for x in t["statements"]):
            if stmt:
                by_repo[t["repo"].lower()].append({"task": t["instance_id"], "issue_text": stmt[:ISSUE_PREFIX]})
        by_repo.setdefault(t["repo"].lower(), [])
    commits = {t["base_commit"]: t["instance_id"] for t in tasks if t["base_commit"]}
    ids = {t["instance_id"] for t in tasks}
    refs = {}
    for t in tasks:
        for num in [t["number"], *t["issue_numbers"]]:
            refs[(t["repo"].lower(), num)] = t["instance_id"]

    n = 0
    repo_rows = Counter()
    hits = defaultdict(lambda: defaultdict(set))  # task -> uuid -> keys
    for uuid, repo, users, raw in rows:
        n += 1
        repo_l = (repo or "").lower()
        if repo_l:
            repo_rows[repo_l] += 1
        # keys found anywhere in the example, by token extraction (one pass each)
        for h in set(HEX40.findall(raw)) & commits.keys():
            hits[commits[h]][uuid].add("base_commit")
        for tid in set(INSTANCE.findall(raw)) & ids:
            hits[tid][uuid].add("instance_id")
        for r, num in set(GHREF.findall(raw)):
            tid = refs.get((r.lower(), num))
            if tid:
                hits[tid][uuid].add("github_ref")
        # issue text: against the example's own repo, or every repo when it has none
        cands = by_repo.get(repo_l, []) if repo_l else [c for cs in by_repo.values() for c in cs]
        if cands:
            user_norm = [norm(u) for u in users]
            for c in cands:
                if any(c["issue_text"] in u for u in user_norm):
                    hits[c["task"]][uuid].add("issue_text")

    overlap = sorted(set(repo_rows) & set(by_repo))
    hit_list = [{"task": t, "uuid": u, "keys": sorted(k)} for t, us in sorted(hits.items())
                for u, k in sorted(us.items())]
    return {
        "tasks": len(tasks), "examples": n, "examples_with_repo": sum(repo_rows.values()),
        "repo_overlap": [{"repo": r, "examples": repo_rows[r],
                          "tasks": sum(1 for t in tasks if t["repo"].lower() == r)} for r in overlap],
        "tasks_matched": len(hits),
        "examples_matched": len({h["uuid"] for h in hit_list}),
        "matches_by_key": dict(Counter(k for h in hit_list for k in h["keys"])),
        "hits": hit_list,
        "drop": sorted({h["uuid"] for h in hit_list}),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default=str(REPO / "var" / "contamination"))
    a = ap.parse_args(argv)

    tasks, snap_hash = load_snapshot(a.snapshot)
    root, files, ds_hash = dataset_files(a.dataset)
    out = match(tasks, examples(root, files))
    out = {"snapshot": {"name": a.snapshot, "tree_hash": snap_hash},
           "dataset": {"name": a.dataset, "tree_hash": ds_hash}, **out}
    dest = Path(a.out) / f"{a.dataset}--{a.snapshot}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k not in ("hits", "drop")}, indent=1))
    print(f"report: {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
