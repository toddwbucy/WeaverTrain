"""Run a frozen benchmark snapshot N times and record per-task results.

    run_bench(snapshot_dir, manifest_path, runner, model=..., repetitions=N, out_dir=...)

A snapshot is a directory whose files are listed in a tree manifest (kind
``snapshot``) and which contains ``tasks.jsonl``: one JSON object per line, each with
a unique ``task_id``. The snapshot is verified (full sha256) before any task runs;
a mismatch refuses the run. ``expected_hash`` additionally pins the tree hash.

The bench does not load models or know any harness. It calls a *runner*: any
callable ``runner(task: dict, *, repetition: int, seed: int, snapshot_dir: Path) -> dict``
whose result has a ``status`` (``resolved`` | ``unresolved`` | ``error``) and any
other fields. The Docker SWE harness will be one such runner. A runner exception is
recorded as that task's ``error`` and the run continues.

Output, per repetition ``out_dir/rep-<NN>/``:
  run.json       run record (kind ``bench``; model and snapshot hashes, sampler, seed)
  results.jsonl  one line per task, appended as each finishes

Re-running into the same ``out_dir`` resumes: tasks already in a repetition's
results.jsonl are skipped, provided its run.json names the same snapshot, model,
sampler and seed. Repetition ``r`` uses seed ``seed + r``.

CLI::

    python -m weaver_train.bench SNAPSHOT_DIR MANIFEST --runner pkg.mod:fn \\
        --model-name NAME --model-hash HASH --repetitions 3 --out DIR [--sampler JSON]
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from weaver_train import manifest as mf

STATUSES = ("resolved", "unresolved", "error")
Runner = Callable[..., dict]


class BenchError(Exception):
    pass


def read_tasks(snapshot_dir: Path) -> list[dict]:
    path = snapshot_dir / "tasks.jsonl"
    if not path.is_file():
        raise BenchError(f"snapshot has no tasks.jsonl: {snapshot_dir}")
    tasks, seen = [], set()
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        t = json.loads(line)
        tid = t.get("task_id")
        if not isinstance(tid, str) or not tid:
            raise BenchError(f"tasks.jsonl line {n}: missing task_id")
        if tid in seen:
            raise BenchError(f"tasks.jsonl line {n}: duplicate task_id {tid!r}")
        seen.add(tid)
        tasks.append(t)
    if not tasks:
        raise BenchError("tasks.jsonl is empty")
    return tasks


def _done(results_path: Path) -> dict[str, dict]:
    if not results_path.exists():
        return {}
    out = {}
    for line in results_path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["task_id"]] = r
    return out


def _summary(results: list[dict]) -> dict:
    c = Counter(r["status"] for r in results)
    n = len(results)
    return {"tasks": n, **{s: c.get(s, 0) for s in STATUSES},
            "resolved_rate": (c.get("resolved", 0) / n) if n else None}


def runner_identity(runner) -> str:
    """module:qualname of an importable function, the same spec the CLI takes. A lambda,
    a nested function or a callable object has no stable unique name: pass runner_name."""
    mod, qual = getattr(runner, "__module__", None), getattr(runner, "__qualname__", None)
    if not (inspect.isfunction(runner) and mod and qual and "<" not in qual):
        raise BenchError(f"runner {runner!r} has no stable name; pass runner_name (module:callable)")
    return f"{mod}:{qual}"


def run_bench(snapshot_dir: str | os.PathLike, manifest_path: str | os.PathLike, runner: Runner, *,
              model: dict, out_dir: str | os.PathLike, repetitions: int = 1, seed: int = 0,
              sampler: dict | None = None, adapter: dict | None = None,
              expected_hash: str | None = None, task_ids: list[str] | None = None,
              runner_name: str | None = None) -> list[mf.RunRecord]:
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    if repetitions < 1:
        raise BenchError("repetitions must be >= 1")
    m = mf.load_tree(manifest_path)
    if m.kind != "snapshot":
        raise BenchError(f"{manifest_path} is a {m.kind} manifest, not a snapshot")
    if expected_hash and m.tree_hash != expected_hash:
        raise BenchError(f"snapshot manifest hash {m.tree_hash} != expected {expected_hash}")
    snap_hash = mf.require_tree(m, snapshot_dir)  # refuses on mismatch
    tasks = read_tasks(snapshot_dir)
    if task_ids is not None:
        wanted = set(task_ids)
        unknown = wanted - {t["task_id"] for t in tasks}
        if unknown:
            raise BenchError(f"unknown task ids: {sorted(unknown)}")
        tasks = [t for t in tasks if t["task_id"] in wanted]

    sampler = sampler or {}
    snapshot = {"name": m.name, "tree_hash": snap_hash}
    config = {"runner": runner_name or runner_identity(runner),
              "repetitions": repetitions, "base_seed": seed,
              "task_ids": [t["task_id"] for t in tasks]}
    records = []
    for rep in range(repetitions):
        rep_dir = out_dir / f"rep-{rep:02d}"
        run_path, results_path = rep_dir / "run.json", rep_dir / "results.jsonl"
        rep_seed = seed + rep
        if run_path.exists():
            rec = mf.load_run(run_path)
            same = (rec.snapshot == snapshot and rec.model == model and rec.sampler == sampler
                    and rec.seed == rep_seed and rec.adapter == adapter and rec.config == config)
            if not same:
                raise BenchError(f"{rep_dir} holds a different run; use a fresh out_dir")
        else:
            rec = mf.RunRecord(kind="bench", model=model, snapshot=snapshot, adapter=adapter,
                               seed=rep_seed, sampler=sampler, repetition=rep, config=config)
        rec.status = "started"
        rec.save(run_path)

        done = _done(results_path)
        with open(results_path, "a") as out:
            for task in tasks:
                tid = task["task_id"]
                if tid in done:
                    continue
                t0 = time.monotonic()
                try:
                    res = dict(runner(task, repetition=rep, seed=rep_seed, snapshot_dir=snapshot_dir))
                    if res.get("status") not in STATUSES:
                        raise BenchError(f"runner returned status {res.get('status')!r}")
                except Exception as e:  # noqa: BLE001 - a task failure must not end the run
                    res = {"status": "error", "error": f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc(limit=5)}
                res.update(task_id=tid, repetition=rep, seed=rep_seed,
                           duration_s=round(time.monotonic() - t0, 3))
                out.write(json.dumps(res, sort_keys=True, default=str) + "\n")
                out.flush()
                done[tid] = res

        rec.results = _summary([done[t["task_id"]] for t in tasks])
        rec.status = "completed"
        rec.save(run_path)
        records.append(rec)
    return records


def load_runner(spec: str) -> Runner:
    mod, _, attr = spec.partition(":")
    if not attr:
        raise BenchError(f"runner must be 'module:callable', got {spec!r}")
    return getattr(importlib.import_module(mod), attr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m weaver_train.bench", description=__doc__.splitlines()[0])
    ap.add_argument("snapshot_dir")
    ap.add_argument("manifest")
    ap.add_argument("--runner", required=True, help="module:callable")
    ap.add_argument("--model-name", required=True)
    ap.add_argument("--model-hash", required=True)
    ap.add_argument("--adapter-name")
    ap.add_argument("--adapter-hash")
    ap.add_argument("--repetitions", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sampler", default="{}", help="JSON object")
    ap.add_argument("--expected-hash")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    adapter = {"name": a.adapter_name, "tree_hash": a.adapter_hash} if a.adapter_hash else None
    try:
        recs = run_bench(a.snapshot_dir, a.manifest, load_runner(a.runner),
                         model={"name": a.model_name, "tree_hash": a.model_hash}, adapter=adapter,
                         out_dir=a.out, repetitions=a.repetitions, seed=a.seed,
                         sampler=json.loads(a.sampler), expected_hash=a.expected_hash,
                         runner_name=a.runner)
    except (BenchError, mf.ManifestError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    for r in recs:
        print(f"rep {r.repetition}: {r.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
