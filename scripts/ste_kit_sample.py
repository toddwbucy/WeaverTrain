"""Sample an STE rewriter kit: agent narration and final reports from a dataset.

    python scripts/ste_kit_sample.py --out var/ste/kit-500/source.jsonl
        [--n 500] [--final 50] [--seed 2027] [--mix 11,9,10] [--max-chars 2000]
        [--exclude var/ste/kit-r3/source.jsonl]

One segment per trajectory, so the kit covers --n different examples. A segment is
an assistant message's text (kind "narration") or the message of the closing `finish`
call (kind "final", the agent's final report to the user; --final of them). Lengths
follow --mix, the counts of the 30-segment kit's bins (under 200, 200 to 500, 500 and
over), measured on the text after scripts/ste_convert.py's masking; segments longer
than --max-chars masked or with no prose left after masking are not used. Segments
listed in --exclude (by uuid and locator) are not used.

Writes {"id", "kind", "uuid", "loc", "raw"} per line (dataset text: local only) and
prints the counts. Read-only on the dataset.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import ste_check  # noqa: E402
import ste_convert  # noqa: E402
from weaver_train import manifest as mf  # noqa: E402

BINS = [(0, 200), (200, 500), (500, 10**9)]


def bin_of(n: int) -> int:
    return next(i for i, (lo, hi) in enumerate(BINS) if lo <= n < hi)


def targets(n: int, mix: list[int]) -> list[int]:
    t = [n * m // sum(mix) for m in mix]
    for i in sorted(range(len(mix)), key=lambda i: -(n * mix[i] % sum(mix)))[: n - sum(t)]:
        t[i] += 1
    return t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="nemotron-swe-v1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--final", type=int, default=50)
    ap.add_argument("--seed", type=int, default=2027)
    ap.add_argument("--mix", default="11,9,10")
    ap.add_argument("--max-chars", type=int, default=2000)
    ap.add_argument("--exclude", action="append", default=[])
    a = ap.parse_args(argv)

    spec = tomllib.loads((REPO / "config" / "datasets.toml").read_text())["datasets"][a.dataset]
    m = mf.load_tree(REPO / "manifests" / "datasets" / f"{a.dataset}.json")
    data = [f.path for f in m.files if f.path.endswith(".jsonl")]
    path = Path(os.environ["WEAVER_TRAIN_DATA_STORE"]) / spec["local_dir"] / data[0]
    excluded = {(r["uuid"], json.dumps(r["loc"], sort_keys=True))
                for p in a.exclude for r in map(json.loads, open(p, encoding="utf-8"))}

    rng = random.Random(a.seed)
    total = sum(1 for _ in open(path))
    order = rng.sample(range(total), total)          # every row, in a random order
    rank = {idx: k for k, idx in enumerate(order)}
    want = targets(a.n, [int(x) for x in a.mix.split(",")])
    need_final = a.final
    picked: dict[int, dict] = {}                      # rank -> segment
    have = [0] * len(BINS)
    stats = {"rows_read": 0, "finals_too_long": 0, "excluded": 0}

    def eligible(row):
        out = []
        for loc, text in ste_convert.segments(row):
            if loc["field"] not in ("content", "finish"):
                continue
            if (row["uuid"], json.dumps(loc, sort_keys=True)) in excluded:
                stats["excluded"] += 1
                continue
            body = ste_convert.split_ws(text)[1]
            masked = ste_convert.mask(body)[0]
            if not re.search(r"[A-Za-z]{2,}", ste_check.PLACEHOLDER.sub("", masked)):
                continue
            if len(masked) > a.max_chars:
                if loc["field"] == "finish":
                    stats["finals_too_long"] += 1
                continue
            out.append({"kind": "final" if loc["field"] == "finish" else "narration", "uuid": row["uuid"],
                        "loc": loc, "raw": body, "bin": bin_of(len(masked))})
        return out

    # read rows in file order, keep the ones in sample order; stop once the kit is full
    budget = min(total, 20 * a.n)
    chosen = set(order[:budget])
    rows = {}
    with open(path) as f:
        for idx, line in enumerate(f):
            if idx in chosen:
                rows[rank[idx]] = json.loads(line)
    for k in sorted(rows):
        if sum(have) >= a.n:
            break
        stats["rows_read"] += 1
        segs = eligible(rows[k])
        finals = [s for s in segs if s["kind"] == "final" and have[s["bin"]] < want[s["bin"]]]
        if need_final and finals:
            s = finals[0]
            need_final -= 1
        else:
            pool = [s for s in segs if s["kind"] == "narration" and have[s["bin"]] < want[s["bin"]]]
            # leave room in each bin for the finals still to come (they are long)
            if not pool:
                continue
            s = rng.choice(pool)
        picked[k] = s
        have[s["bin"]] += 1
    if sum(have) < a.n or need_final:
        raise SystemExit(f"kit incomplete: {have} of {want}, finals still needed {need_final}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    segs = list(picked.values())
    rng.shuffle(segs)
    with open(out, "w", encoding="utf-8") as f:
        for n, s in enumerate(segs, 1):
            f.write(json.dumps({"id": f"k{n:03d}", "kind": s["kind"], "uuid": s["uuid"], "loc": s["loc"],
                                "raw": s["raw"]}) + "\n")
    kinds = {k: sum(s["kind"] == k for s in segs) for k in ("narration", "final")}
    by_bin = {f"{lo}-{hi if hi < 10**9 else ''}": [sum(s["bin"] == i and s["kind"] == k for s in segs)
                                                   for k in ("narration", "final")]
              for i, (lo, hi) in enumerate(BINS)}
    print(json.dumps({"out": str(out), "segments": len(segs), "kinds": kinds, "bins [narration, final]": by_bin,
                      "targets": want, **stats}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
