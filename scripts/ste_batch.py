"""Convert whole trajectories to STE100 with a model in headless opencode sessions.

    python scripts/ste_batch.py sample   --name NAME --trajectories N [--seed S] [--max-chars 6000]
                                         [--max-tokens 32768]
                                         [--exclude var/ste/kit-500/source.jsonl ...]
    python scripts/ste_batch.py prepare  --name NAME [--part-size 100] [--prompt prompt-<sha12>.json]
    python scripts/ste_batch.py run      --name NAME --model opencode/gemini-4-argon#high --budget USD
                                         [--parts p001,p002 | --limit K] [--repairs 2] [--jobs 4]
    python scripts/ste_batch.py status   --name NAME
    python scripts/ste_batch.py assemble --name NAME

The rewriter-selection kits (scripts/ste_experiment.py) took one segment per
trajectory. A training example needs every agent segment of its trajectory converted,
so this takes whole trajectories:

- sample: N dataset rows, at most one per uuid (4,000 uuids repeat with different
  rows, so a row is named by its line number), none whose uuid is in an --exclude kit.
  A row is used only if it has a final report (`finish`), every narration and
  final segment is at most --max-chars after masking, and the whole row is at most
  --max-tokens under the model's chat template (the trainer's sequence length; most
  rows are longer, so this favors shorter tasks; sample.json records how many). Issue text and `think` fields
  are not segments (the 2026-11-01 scope candidate). Writes var/ste/batch-NAME/
  source.jsonl (dataset text: local only) and prints what was skipped and why.
- prepare: masks every segment, puts whole trajectories into parts of about
  --part-size records, and writes experiments/ste-batch-NAME/pNNN/ with input.jsonl,
  RULES.md, PROMPT.md and REPAIR-PROMPT.md (the 100-segment kit's prompts), and an
  opencode.json that denies shell, web and other directories (see PERMISSIONS). The masked
  spans go to var/ste/batch-NAME/codes.jsonl.
- run: works the parts in order, --jobs at a time. Per part, one fresh `opencode run` session writes
  round1.jsonl; the checker writes repair-1.jsonl; a fresh session writes round2.jsonl;
  and so on, up to --repairs. Before each session the spend on this batch (opencode's
  own session costs, read from its database) is compared with --budget, and the run
  pauses at the cap (a paused part resumes on the next run). With --jobs J the cap can be
  passed by up to J sessions already running. Any script that changes text by pattern, or more than 5% of a
  round's records near-copied from the input, stops the whole run (stop and report).
  An incomplete round gets one session to finish it; if still short, the part is
  marked failed and the run goes on.
- status: per part: rounds, pass counts, cost, state.
- assemble: a trajectory converts only if every segment passes; its rows get the
  rewrites with the masked spans restored, everything else byte-identical. Writes
  var/ste/batch-NAME/converted.jsonl, failed.jsonl and run.json.

Read-only on the dataset and on opencode's database.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import ste_check  # noqa: E402
import ste_convert  # noqa: E402
import ste_experiment as exp  # noqa: E402
from weaver_train import manifest as mf  # noqa: E402

DEFAULT_PROMPT = "prompt-e21370fcd140.json"   # the 100-segment kit's prompt
# Each part's opencode.json: `opencode run --auto` approves every permission that is not
# explicitly denied, so these are denied. With no shell, a session cannot run a script:
# it reads and writes with opencode's own file tools, inside its part directory only.
NO_SHELL = ("\nThis session has no shell. Read and write files with your file tools only. Write each\n"
            "batch of results into the JSON file yourself.\n")
PERMISSIONS = {"$schema": "https://opencode.ai/config.json",
               "permission": {"bash": "deny", "webfetch": "deny", "websearch": "deny",
                              "external_directory": "deny"}}
NEAR_COPY_LIMIT = 0.05


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def var_dir(name: str) -> Path:
    return REPO / "var" / "ste" / f"batch-{name}"


def exp_dir(name: str) -> Path:
    return REPO / "experiments" / f"ste-batch-{name}"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def dataset_file(dataset: str) -> tuple[Path, dict]:
    spec = tomllib.loads((REPO / "config" / "datasets.toml").read_text())["datasets"][dataset]
    m = mf.load_tree(REPO / "manifests" / "datasets" / f"{dataset}.json")
    data = [f.path for f in m.files if f.path.endswith(".jsonl")]
    return Path(os.environ["WEAVER_TRAIN_DATA_STORE"]) / spec["local_dir"] / data[0], spec


# ---------------------------------------------------------------- sample

def agent_segments(row: dict, max_chars: int) -> tuple[list[dict] | None, str]:
    """The narration and final segments of a row, or (None, reason) if it is not usable."""
    out = []
    for loc, text in ste_convert.segments(row):
        if loc["field"] not in ("content", "finish"):
            continue
        body = ste_convert.split_ws(text)[1]
        if "[[C" in body:
            return None, "marker_collision"
        masked = ste_convert.mask(body)[0]
        if len(masked) > max_chars:
            return None, "segment_too_long"
        prose = bool(re.search(r"[A-Za-z]{2,}", ste_check.PLACEHOLDER.sub("", masked)))
        out.append({"kind": "final" if loc["field"] == "finish" else "narration", "loc": loc,
                    "raw": text, "prose": prose})
    if not any(s["kind"] == "final" for s in out):
        return None, "no_final_report"
    if not any(s["prose"] for s in out):
        return None, "no_prose"
    return out, ""


class TokenCounter:
    """Tokens of a whole row under the model's own chat template: the length the
    trainer sees. Reads only the tokenizer files of the model tree."""

    def __init__(self, model: str):
        from transformers import AutoTokenizer
        spec = tomllib.loads((REPO / "config" / "models.toml").read_text())["models"][model]
        for var in ("WEAVER_TRAIN_MODEL_CACHE", "WEAVER_TRAIN_MODEL_STORE"):
            d = Path(os.environ.get(var, "")) / spec["local_dir"]
            if os.environ.get(var) and d.is_dir():
                break
        else:
            raise SystemExit("no model directory; set WEAVER_TRAIN_MODEL_CACHE or WEAVER_TRAIN_MODEL_STORE")
        self.tok = AutoTokenizer.from_pretrained(d)

    def __call__(self, row: dict) -> int:
        msgs = []
        for m in row["messages"]:
            m = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
            if m.get("tool_calls"):
                m["tool_calls"] = [{**tc, "function": {**tc["function"], "arguments": json.loads(
                    tc["function"]["arguments"]) if isinstance(tc["function"].get("arguments"), str)
                    else tc["function"].get("arguments")}} for tc in m["tool_calls"]]
            msgs.append(m)
        return len(self.tok(self.tok.apply_chat_template(msgs, tokenize=False))["input_ids"])


def cmd_sample(a) -> int:
    out_dir = var_dir(a.name)
    if (out_dir / "source.jsonl").exists():
        raise SystemExit(f"{out_dir / 'source.jsonl'} exists; choose a new --name")
    path, _ = dataset_file(a.dataset)
    excluded = {r["uuid"] for p in a.exclude for r in read_jsonl(Path(p))}
    total = sum(1 for _ in open(path))
    order = random.Random(a.seed).sample(range(total), total)
    want = set(order[: min(total, a.read_factor * a.trajectories)])
    count = TokenCounter(a.model) if a.max_tokens else None
    rows = {}
    with open(path) as f:
        for idx, line in enumerate(f):
            if idx in want:
                rows[idx] = line
    skipped: dict[str, int] = {}
    seen: set[str] = set()
    picked = []
    for idx in order:
        if len(picked) == a.trajectories:
            break
        if idx not in rows:
            raise SystemExit("ran out of candidate rows; raise --read-factor")
        row = json.loads(rows[idx])
        why = ("excluded" if row["uuid"] in excluded else "repeated_uuid" if row["uuid"] in seen else "")
        segs = None
        if not why:
            segs, why = agent_segments(row, a.max_chars)
        if not why and count and count(row) > a.max_tokens:
            why = "over_max_tokens"
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        seen.add(row["uuid"])
        picked.append((idx, row["uuid"], segs))
    out_dir.mkdir(parents=True, exist_ok=True)
    n_seg = 0
    with open(out_dir / "source.jsonl", "w", encoding="utf-8") as f:
        for t, (idx, uuid, segs) in enumerate(picked, 1):
            for s, seg in enumerate(segs, 1):
                f.write(json.dumps({"id": f"T{t:03d}.{s:03d}", "traj": f"T{t:03d}", "row": idx, "uuid": uuid,
                                    **seg}) + "\n")
                n_seg += 1
    prose = sum(s["prose"] for _, _, segs in picked for s in segs)
    meta = {"made_at": now(), "dataset": a.dataset, "seed": a.seed, "trajectories": len(picked),
            "segments": n_seg, "prose_segments": prose, "max_chars": a.max_chars, "max_tokens": a.max_tokens,
            "exclude": [Path(p).name for p in a.exclude], "skipped": skipped,
            "rows_considered": len(picked) + sum(skipped.values())}
    (out_dir / "sample.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta, indent=1))
    return 0


# ---------------------------------------------------------------- prepare

def cmd_prepare(a) -> int:
    src = read_jsonl(var_dir(a.name) / "source.jsonl")
    root = exp_dir(a.name)
    if root.exists():
        raise SystemExit(f"{root} exists")
    prompt_path = REPO / "var" / "ste" / a.prompt
    prompt = json.loads(prompt_path.read_text())
    trajs: dict[str, list[dict]] = {}
    for r in src:
        trajs.setdefault(r["traj"], []).append(r)
    parts: list[list[dict]] = [[]]
    for segs in trajs.values():
        if parts[-1] and sum(s["prose"] for s in parts[-1]) >= a.part_size:
            parts.append([])
        parts[-1].extend(segs)
    root.mkdir(parents=True)
    with open(var_dir(a.name) / "codes.jsonl", "w", encoding="utf-8") as fc:
        for k, segs in enumerate(parts, 1):
            d = root / f"p{k:03d}"
            d.mkdir()
            recs = []
            for s in segs:
                lead, body, trail = ste_convert.split_ws(s["raw"])
                masked, codes = ste_convert.mask(body)
                fc.write(json.dumps({"id": s["id"], "lead": lead, "trail": trail, "codes": codes}) + "\n")
                if s["prose"]:
                    recs.append({"id": s["id"], "kind": s["kind"], "text": masked})
            with open(d / "input.jsonl", "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            kinds = sorted({r["kind"] for r in recs}, key=list(exp.KIND_TEXT).index)
            kinds_line = ("The kind is " if len(kinds) == 1 else "The kinds are ") + ", ".join(
                exp.KIND_TEXT[x] for x in kinds) + "."
            batched = "" if len(recs) < 100 else (
                " It is long: read it 10 records at a time (the read tool with a line\n"
                "   offset), not all at once. Before each 100 records, read RULES.md again.")
            (d / "RULES.md").write_text(exp.render_rules(prompt))
            (d / "opencode.json").write_text(json.dumps(PERMISSIONS, indent=1) + "\n")
            (d / "PROMPT.md").write_text(exp.PROMPT.replace("@N@", str(len(recs))).replace("@KINDS@", kinds_line)
                                         .replace("@BATCHED@", batched) + NO_SHELL)
            (d / "REPAIR-PROMPT.md").write_text(exp.REPAIR_PROMPT + NO_SHELL)
            (d / "part.json").write_text(json.dumps({
                "made_at": now(), "prompt": a.prompt, "prompt_sha256": prompt["meta"]["prompt_sha256"],
                "records": len(recs), "trajectories": sorted({s["traj"] for s in segs}),
                "input_sha256": exp.sha256(d / "input.jsonl"), "state": "new", "rounds": {}}, indent=1) + "\n")
    print(f"{root}: {len(parts)} parts, {sum(len(p) for p in parts)} segments "
          f"({sum(s['prose'] for p in parts for s in p)} to rewrite), {len(trajs)} trajectories")
    return 0


# ---------------------------------------------------------------- run

def opencode_bin() -> str:
    found = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    if not Path(found).exists():
        raise SystemExit("opencode not found; set OPENCODE_BIN")
    return found


def opencode_db() -> Path:
    return Path(os.environ.get("OPENCODE_DB") or Path.home() / ".local/share/opencode/opencode.db")


def spent(root: Path, under: Path | None = None) -> float:
    """Cost of every opencode session whose directory is under ROOT (or UNDER), in USD."""
    con = sqlite3.connect(f"file:{opencode_db()}?mode=ro", uri=True)
    try:
        prefix = str(under or root)
        (c,) = con.execute("select coalesce(sum(cost), 0) from session_v2 where directory = ? or directory like ?",
                           (prefix, prefix + "/%")).fetchone()
    finally:
        con.close()
    return float(c)


class Stop(Exception):
    """Stop the whole run and report: a part broke a rule."""


class Paused(Exception):
    """The budget is reached or another part stopped the run: this part can resume."""


def session(d: Path, model: str, message: str, title: str, timeout: int) -> int:
    if json.loads((d / "opencode.json").read_text()) != PERMISSIONS:
        raise Stop(f"{d.name}: opencode.json is not the permission policy; refusing --auto")
    logs = d / "logs"
    logs.mkdir(exist_ok=True)
    with open(logs / f"{title}.log", "w", encoding="utf-8") as log:
        p = subprocess.run([opencode_bin(), "run", "-m", model, "--auto", "--title", title, message],
                           cwd=d, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode


def text_of(inputs: list[dict], i: str) -> str:
    return next(r["text"] for r in inputs if r["id"] == i)


def guard(d: Path, inputs: list[dict], n: int, judge) -> None:
    if (bad := exp.substitution_scripts(d)):
        raise Stop(f"{d.name}: text changed by script {bad}")
    out, _ = exp.merged(d, n)
    src = inputs if n == 1 else read_jsonl(d / f"repair-{n - 1}.jsonl")
    ids = {r["id"] for r in src}
    # an input that already passes may come back unchanged; a failing one may not
    copies = [i for i in exp.near_copies(out, inputs) if i in ids and not judge(i_text := text_of(inputs, i), i_text)["ok"]]
    if len(copies) > NEAR_COPY_LIMIT * len(src):
        raise Stop(f"{d.name}: round {n}: {len(copies)} of {len(src)} outputs are near-copies of the input")


def judge_round(d: Path, inputs: list[dict], judge) -> tuple[int, list[dict]]:
    out, _ = exp.merged(d)
    fails = []
    for r in inputs:
        res = judge(out.get(r["id"]), r["text"])
        if not res["ok"]:
            note = ste_check.repair_note(res, judge.lex)
            if "invented_markup" in res["hard"]:
                note += ("\n- Your rewrite has markup that is not in the text: "
                         + ", ".join(res["hard"]["invented_markup"]) + ". Remove it.")
            fails.append({"id": r["id"], "kind": r["kind"], "text": r["text"], "previous": out.get(r["id"], ""),
                          "problems": note.strip() or "- " + ", ".join(res["hard"])})
    return len(inputs) - len(fails), fails


def work_part(d: Path, a, judge, root: Path) -> dict:
    """Judge the newest round if it is not judged yet; else start the session the part
    owes (round 1, or a repair round while records fail and repairs are left)."""
    meta = json.loads((d / "part.json").read_text())
    inputs = read_jsonl(d / "input.jsonl")
    if exp.sha256(d / "input.jsonl") != meta["input_sha256"]:
        raise Stop(f"{d.name}: input.jsonl changed")
    meta["started_at"] = meta.get("started_at") or now()
    meta["state"] = "running"
    while True:
        rs = exp.rounds(d)
        last = rs[-1] if rs else 0
        if last and not (d / f"repair-{last}.jsonl").exists():
            guard(d, inputs, last, judge)
            ok, fails = judge_round(d, inputs, judge)
            with open(d / f"repair-{last}.jsonl", "w", encoding="utf-8") as f:
                for x in fails:
                    f.write(json.dumps(x) + "\n")
            meta["rounds"].setdefault(str(last), {})["pass"] = ok
            (d / "part.json").write_text(json.dumps(meta, indent=1) + "\n")
            print(f"{d.name}: round {last}: {ok}/{len(inputs)} pass", flush=True)
            continue
        if last and (not read_jsonl(d / f"repair-{last}.jsonl") or last >= a.repairs + 1):
            meta["state"] = "done"
            break
        n = last + 1
        if HALT.is_set():
            raise Paused("the run was halted")
        if (s := spent(root)) >= a.budget:
            raise Paused(f"budget reached: ${s:.2f} of ${a.budget:.2f} spent")
        msg = ("Read PROMPT.md and do exactly what it says." if n == 1 else
               f"Read REPAIR-PROMPT.md and do exactly what it says. Use repair-{n - 1}.jsonl and write "
               f"round{n}.jsonl.")
        t0 = time.time()
        rc = session(d, a.model, msg, f"{d.name}-round{n}", a.timeout)
        if (d / f"round{n}.jsonl").exists() and (short := exp.incomplete(d, n)):
            session(d, a.model, f"round{n}.jsonl has {short[0]} of {short[1]} records. Write the missing "
                    f"records as {'PROMPT.md' if n == 1 else 'REPAIR-PROMPT.md'} says, then stop.",
                    f"{d.name}-round{n}-finish", a.timeout)
        meta["rounds"][str(n)] = {"exit": rc, "seconds": round(time.time() - t0)}
        if not (d / f"round{n}.jsonl").exists() or (short := exp.incomplete(d, n)):
            meta["state"] = f"failed: round{n} missing or incomplete"
            break
    meta["finished_at"] = now()
    meta["cost_usd"] = round(spent(root, d), 4)
    (d / "part.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


HALT = threading.Event()   # set when any part stops the run: no part starts another session


def cmd_run(a) -> int:
    root = exp_dir(a.name)
    parts = sorted(p for p in root.glob("p[0-9][0-9][0-9]") if p.is_dir())
    if a.parts:
        names = set(a.parts.split(","))
        parts = [p for p in parts if p.name in names]
    parts = [p for p in parts if json.loads((p / "part.json").read_text())["state"].split(":")[0]
             in ("new", "running", "paused")]
    if a.limit:
        parts = parts[: a.limit]
    judge = exp.Judge(json.loads((parts[0] / "part.json").read_text())["prompt"]) if parts else None
    print(f"{len(parts)} parts to run, {a.jobs} at a time; spent so far ${spent(root):.2f} of ${a.budget:.2f}",
          flush=True)
    HALT.clear()
    stops: list[str] = []

    def one(d: Path) -> None:
        if HALT.is_set():
            return
        try:
            meta = work_part(d, a, judge, root)
        except (Stop, Paused) as e:
            meta = json.loads((d / "part.json").read_text())
            meta["state"] = f"{'paused' if isinstance(e, Paused) else 'stopped'}: {e}"
            (d / "part.json").write_text(json.dumps(meta, indent=1) + "\n")
            if isinstance(e, Stop):
                stops.append(str(e))
                print(f"STOP: {e}", flush=True)
            HALT.set()
            return
        print(f"{d.name}: {meta['state']}, ${meta['cost_usd']:.2f}; batch ${spent(root):.2f}", flush=True)

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        list(pool.map(one, parts))
    if HALT.is_set():
        print(f"halted: {stops or 'budget reached'}; spent ${spent(root):.2f}", flush=True)
        return 2
    return 0


# ---------------------------------------------------------------- status

def cmd_status(a) -> int:
    root = exp_dir(a.name)
    print("| Part | Records | Trajectories | Rounds (pass) | Cost | State |")
    print("|---|---|---|---|---|---|")
    for d in sorted(root.glob("p[0-9][0-9][0-9]")):
        m = json.loads((d / "part.json").read_text())
        rounds = ", ".join(f"{k}: {v.get('pass', '-')}" for k, v in sorted(m["rounds"].items()))
        print(f"| {d.name} | {m['records']} | {len(m['trajectories'])} | {rounds or '-'} | "
              f"${m.get('cost_usd', 0):.2f} | {m['state']} |")
    print(f"\nspent on this batch: ${spent(root):.2f}")
    return 0


# ---------------------------------------------------------------- assemble

def gemini_cost(vd: Path) -> float:
    """Batch API cost estimates that scripts/ste_gemini.py fetch recorded."""
    return sum(json.loads(p.read_text()).get("cost_usd_batch", 0) for p in (vd / "gemini").glob("round*.json"))


def cmd_assemble(a) -> int:
    root, vd = exp_dir(a.name), var_dir(a.name)
    src = read_jsonl(vd / "source.jsonl")
    codes = {r["id"]: r for r in read_jsonl(vd / "codes.jsonl")}
    out: dict[str, str] = {}
    passed: set[str] = set()
    prompt = None
    for d in sorted(root.glob("p[0-9][0-9][0-9]")):
        m = json.loads((d / "part.json").read_text())
        prompt = prompt or m["prompt"]
        if m["state"] != "done":
            continue
        judge = exp.Judge(m["prompt"])
        merged, _ = exp.merged(d)
        for r in read_jsonl(d / "input.jsonl"):
            o = merged.get(r["id"])
            if judge(o, r["text"])["ok"] and not exp.altered_literals(o, r["text"]):
                out[r["id"]] = o
                passed.add(r["id"])
    trajs: dict[str, list[dict]] = {}
    for s in src:
        trajs.setdefault(s["traj"], []).append(s)
    good = {t: segs for t, segs in trajs.items() if all(not s["prose"] or s["id"] in passed for s in segs)}
    path, spec = dataset_file(a.dataset)
    want = {segs[0]["row"]: t for t, segs in good.items()}
    rows = {}
    with open(path) as f:
        for idx, line in enumerate(f):
            if idx in want:
                rows[want[idx]] = json.loads(line)
    prompt_sha = json.loads((REPO / "var" / "ste" / prompt).read_text())["meta"]["prompt_sha256"] if prompt else None
    with open(vd / "converted.jsonl", "w", encoding="utf-8") as fo:
        for t, segs in good.items():
            row = copy.deepcopy(rows[t])
            assert row["uuid"] == segs[0]["uuid"], t
            for s in sorted(segs, key=lambda s: -s["loc"]["msg"]):
                if not s["prose"]:
                    continue
                c = codes[s["id"]]
                ste_convert.apply(row, s["loc"], c["lead"] + ste_convert.unmask(out[s["id"]], c["codes"]) + c["trail"])
            row["ste"] = {"batch": a.name, "trajectory": t, "row": segs[0]["row"], "prompt_sha256": prompt_sha}
            fo.write(json.dumps(row) + "\n")
    with open(vd / "failed.jsonl", "w", encoding="utf-8") as ff:
        for t, segs in trajs.items():
            if t not in good:
                ff.write(json.dumps({"traj": t, "row": segs[0]["row"], "uuid": segs[0]["uuid"],
                                     "failed": [s["id"] for s in segs if s["prose"] and s["id"] not in passed]}) + "\n")
    record = {"assembled_at": now(), "batch": a.name, "dataset": a.dataset, "revision": spec["revision"],
              "prompt": prompt, "prompt_sha256": prompt_sha, "trajectories": len(trajs), "converted": len(good),
              "segments_passed": len(passed), "segments_to_rewrite": sum(s["prose"] for s in src),
              "cost_usd": round(spent(root) + gemini_cost(vd), 2), "converted_sha256": exp.sha256(vd / "converted.jsonl")}
    (vd / "run.json").write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--trajectories", type=int, required=True)
    s.add_argument("--seed", type=int, default=2031)
    s.add_argument("--max-chars", type=int, default=6000)
    s.add_argument("--exclude", action="append", default=[])
    s.add_argument("--max-tokens", type=int, default=32768,
                   help="whole row, chat template applied, the model's tokenizer (0: no limit)")
    s.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    s.add_argument("--read-factor", type=int, default=40, help="candidate rows read per trajectory wanted")
    p = sub.add_parser("prepare")
    p.add_argument("--part-size", type=int, default=100)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    r = sub.add_parser("run")
    r.add_argument("--model", required=True, help="provider/model#variant, as opencode run -m takes it")
    r.add_argument("--budget", type=float, required=True, help="stop before a session once this many USD are spent")
    r.add_argument("--parts", help="comma-separated part names (default: every part not yet done)")
    r.add_argument("--limit", type=int, help="run at most this many parts")
    r.add_argument("--repairs", type=int, default=2)
    r.add_argument("--jobs", type=int, default=1, help="parts worked at once (each its own session)")
    r.add_argument("--timeout", type=int, default=3600, help="seconds per session")
    sub.add_parser("status")
    sub.add_parser("assemble")
    for x in sub.choices.values():
        x.add_argument("--name", required=True)
        x.add_argument("--dataset", default="nemotron-swe-v1")
    a = ap.parse_args(argv)
    return {"sample": cmd_sample, "prepare": cmd_prepare, "run": cmd_run, "status": cmd_status,
            "assemble": cmd_assemble}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
