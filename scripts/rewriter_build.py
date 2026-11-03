"""Spec 0.1 PAIR and RENDER clauses: collect pairs and render final-target masks.

The pairs command reads local evidence only. Rendering functions have no output
command until the RENDER-SEED record interface and folded prompt pin are agreed.
No function here loads a model, rewrites text, or contacts a service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]

import ste_check
import ste_convert
import ste_experiment as experiment
from ste_kit_sample import targets
from weaver_train import manifest as mf

# PAIR-INPUTS, PAIR-PROVENANCE, PAIR-BALANCE: fixed inputs and proposals.
BATCHES = ("200", "200-2")
PROMPT = "prompt-e21370fcd140.json"
WEIGHTS = [37, 30, 33]
PROVENANCE = ("grouped_instruction", "single_text_bundle", "system_text")
# RENDER-LENGTH: the approved context has no per-invocation override.
MAX_SEQ_LENGTH = 49152


class PairError(ValueError):
    """PAIR-INPUTS, PAIR-OUTPUT: refuse ambiguous evidence or an unsafe output."""


def require(condition, message):
    """PAIR-INPUTS: stop on an integrity failure with its clause and source."""
    if not condition:
        raise PairError(message)


def digest(data: bytes) -> str:
    """PAIR-INPUTS, PAIR-BALANCE: hash exact bytes with SHA256."""
    return hashlib.sha256(data).hexdigest()


@dataclass
class Entry:
    """PAIR-INPUTS, PAIR-REPAIRS: original JSON and its physical file location."""

    data: dict
    ref: dict


class Evidence:
    """PAIR-INPUTS: record every file read and detect changes during collection."""

    def __init__(self, repo: Path):
        """PAIR-INPUTS: start one immutable input inventory."""
        self.repo = repo
        self.files = {}

    def read(self, relative, expected=None):
        """PAIR-INPUTS: check the digest before parsing the exact file bytes."""
        name = str(relative)
        raw = (self.repo / name).read_bytes()
        sha = digest(raw)
        require(expected is None or sha == expected,
                f"PAIR-INPUTS: sha256 differs for {name}")
        require(name not in self.files or self.files[name]["sha256"] == sha,
                f"PAIR-INPUTS: file changed during collection: {name}")
        self.files[name] = {"sha256": sha, "bytes": len(raw)}
        return raw

    def json(self, relative, expected=None):
        """PAIR-INPUTS: a metadata record must be a JSON object."""
        value = json.loads(self.read(relative, expected))
        require(isinstance(value, dict), f"PAIR-INPUTS: object required: {relative}")
        return value

    def rows(self, relative, expected=None):
        """PAIR-INPUTS, PAIR-REPAIRS: retain original physical line references."""
        raw = self.read(relative, expected)
        out = []
        for line, text in enumerate(raw.splitlines(), 1):
            if text.strip():
                value = json.loads(text)
                require(isinstance(value, dict),
                        f"PAIR-INPUTS: object required: {relative}:{line}")
                out.append(Entry(value, {"file": str(relative), "line": line}))
        return out

    def verify(self):
        """PAIR-INPUTS: no input may change before the pair record is written."""
        for name, record in self.files.items():
            require(mf.sha256_file(self.repo / name) == record["sha256"],
                    f"PAIR-INPUTS: file changed during collection: {name}")


def unique(entries, field="id"):
    """PAIR-INPUTS: never let a repeated ID silently replace an earlier row."""
    indexed = {}
    for entry in entries:
        key = entry.data[field]
        require(isinstance(key, str) and key and key not in indexed,
                f"PAIR-INPUTS: duplicate or invalid {field}: {entry.ref}")
        indexed[key] = entry
    return indexed


def prompt_inputs(evidence, pins):
    """PAIR-PROVENANCE, PAIR-TARGET: verify provenance and the checker dictionary."""
    evidence.read("var/ste/approved-verbs.json", pins["approved_verbs"])
    prompt = evidence.json(f"var/ste/{PROMPT}")
    system_hash = digest(prompt["system"].encode("utf-8"))
    bundle_hash = digest(json.dumps([prompt["system"], prompt["examples"]]).encode())
    require(system_hash == pins["system_text"], "PAIR-PROVENANCE: system hash differs")
    require(bundle_hash == pins["single_text_bundle"],
            "PAIR-PROVENANCE: bundle hash differs")
    require(prompt["meta"]["prompt_sha256"] == bundle_hash,
            "PAIR-PROVENANCE: bundle metadata differs")
    require(prompt["meta"]["verbs_sha256"] == pins["approved_verbs"],
            "PAIR-TARGET: prompt dictionary hash differs")
    for path in sorted((evidence.repo / "ste").glob("*.toml")):
        evidence.read(path.relative_to(evidence.repo))
    evidence.read("scripts/ste_check.py")
    evidence.read("scripts/ste_convert.py")
    evidence.read("scripts/ste_experiment.py")
    return {key: pins[key] for key in PROVENANCE}


def load_parts(evidence, batch, pins):
    """PAIR-INPUTS, PAIR-TARGET: read numeric rounds without last-pass fallback."""
    root = Path("experiments") / f"ste-batch-{batch}"
    parts, owners = {}, {}
    for directory in sorted((evidence.repo / root).glob("p[0-9][0-9][0-9]")):
        part = root / directory.name
        meta = evidence.json(part / "part.json")
        require(meta["prompt"] == PROMPT and
                meta["prompt_sha256"] == pins["single_text_bundle"],
                f"PAIR-PROVENANCE: part prompt differs: {part}")
        inputs = unique(evidence.rows(part / "input.jsonl", meta["input_sha256"]))
        require(meta["records"] == len(inputs),
                f"PAIR-INPUTS: part count differs: {part}")
        require(not owners.keys() & inputs.keys(),
                f"PAIR-INPUTS: duplicate batch segment in {part}")
        owners.update({key: directory.name for key in inputs})
        rounds, requests, previous = {}, {}, {}
        paths = list(directory.glob("round*.jsonl"))
        require(all(re.fullmatch(r"round[1-4]\.jsonl", p.name) for p in paths),
                f"PAIR-TARGET: unsupported round in {part}")
        for path in sorted(paths, key=lambda p: int(p.stem[5:])):
            number = int(path.stem[5:])
            source = part / ("input.jsonl" if number == 1
                             else f"repair-{number - 1}.jsonl")
            request = inputs if number == 1 else unique(evidence.rows(source))
            require(request.keys() <= inputs.keys(),
                    f"PAIR-INPUTS: unknown repair ID in {source}")
            for key, entry in request.items():
                require(entry.data["text"] == inputs[key].data["text"] and
                        entry.data["kind"] == inputs[key].data["kind"],
                        f"PAIR-REPAIRS: source changed: {source}:{key}")
                if number > 1:
                    require(entry.data["previous"] == previous.get(key, "") and
                            isinstance(entry.data["problems"], str),
                            f"PAIR-REPAIRS: previous output differs: {source}:{key}")
            outputs = unique(evidence.rows(part / path.name))
            require(outputs.keys() <= request.keys(),
                    f"PAIR-INPUTS: unrequested output in {part / path.name}")
            require(all(isinstance(x.data["output"], str) for x in outputs.values()),
                    f"PAIR-TARGET: non-text output in {part / path.name}")
            previous.update({key: entry.data["output"] for key, entry in outputs.items()})
            rounds[number], requests[number] = outputs, request
        require(1 in rounds, f"PAIR-TARGET: round 1 absent from {part}")
        parts[directory.name] = {"meta": meta, "inputs": inputs,
                                 "outputs": rounds, "requests": requests}
    require(parts, f"PAIR-INPUTS: no parts for batch {batch}")
    return parts, owners


def grouped_requests(evidence, batch, parts, pins):
    """PAIR-PROVENANCE, PAIR-REPAIRS: retain the original grouped request bytes."""
    root = Path("var/ste") / f"batch-{batch}" / "gemini"
    found = {}
    for number in sorted({n for part in parts.values() for n in part["requests"]}):
        state = evidence.json(root / f"round{number}.json")
        require(state["name"] == batch and state["round"] == number and
                state["prompt"] == PROMPT and type(state["group"]) is int and
                state["group"] > 1,
                f"PAIR-PROVENANCE: grouped request metadata differs: {root}")
        groups = unique(evidence.rows(root / f"round{number}.jsonl",
                                      state["input_sha256"]), "key")
        expected, total = set(), 0
        for name, part in parts.items():
            rows = list(part["requests"].get(number, {}).values())
            # PAIR-REPAIRS: an empty local round can hold later checker notes.
            # Only the saved request payload establishes a submitted request.
            if not any(key.startswith(name + "/") for key in groups):
                require(not part["outputs"].get(number),
                        f"PAIR-REPAIRS: output has no original request: {batch}/{name}, "
                        f"round {number}")
                continue
            total += len(rows)
            for offset in range(0, len(rows), state["group"]):
                members = rows[offset:offset + state["group"]]
                key = f"{name}/{members[0].data['id']}"
                expected.add(key)
                require(key in groups,
                        f"PAIR-REPAIRS: original request absent: {batch}/{key}, round {number}")
                group = groups[key]
                instruction = group.data["request"]["systemInstruction"]["parts"]
                require(len(instruction) == 1 and
                        digest(instruction[0]["text"].encode()) == pins["grouped_instruction"],
                        f"PAIR-PROVENANCE: grouped instruction differs: {key}")
                for member in members:
                    found[number, member.data["id"]] = group
        require(expected == groups.keys() and state["records"] == total and
                state["requests"] == len(groups),
                f"PAIR-REPAIRS: request membership differs: {root}, round {number}")
    return found


def converted_text(row, source):
    """PAIR-TARGET: only narration content and finish messages can be targets."""
    loc = source["loc"]
    message = row["messages"][loc["msg"]]
    require(message["role"] == "assistant", "PAIR-TARGET: target is not assistant text")
    if source["kind"] == "narration" and loc["field"] == "content":
        return message["content"]
    require(source["kind"] == "final" and loc["field"] == "finish",
            "PAIR-TARGET: target is outside narration and final reports")
    function = message["tool_calls"][loc["call"]]["function"]
    require(function["name"] == "finish", "PAIR-TARGET: target is not a finish message")
    return json.loads(function["arguments"])["message"]


def retain_pair(batch, source, code, row, part, groups, judge):
    """PAIR-TARGET, PAIR-REPAIRS: exclude bad final outputs without changing text."""
    s = source.data
    identity = {"batch": batch, "id": s["id"], "trajectory": s["traj"],
                "uuid": s["uuid"], "row": s["row"], "kind": s["kind"], "loc": s["loc"]}
    lead, body, trail = ste_convert.split_ws(s["raw"])
    masked, codes = ste_convert.mask(body)
    entry = part["inputs"][s["id"]]
    if (entry.data != {"id": s["id"], "kind": s["kind"], "text": masked} or
            code.data != {"id": s["id"], "lead": lead, "trail": trail, "codes": codes}):
        return None, None, {**identity, "reason": "source_mask_or_map"}
    outputs = [(n, rows[s["id"]]) for n, rows in part["outputs"].items()
               if s["id"] in rows]
    if not outputs:
        return None, None, {**identity, "reason": "missing_target"}
    number, output = outputs[-1]
    target = output.data["output"]
    checked = judge(target, masked)
    literals = experiment.altered_literals(target, masked)
    if not checked["ok"] or literals:
        return None, None, {**identity, "reason": "target_check", "round": number,
                            "checks": checked, "altered_literals": literals}
    restored = lead + ste_convert.unmask(target, codes) + trail
    if restored != converted_text(row, s):
        return None, None, {**identity, "reason": "restored_target", "round": number}
    pair = {**identity, "input": masked, "target": target, "target_round": number,
            "codes": codes, "lead": lead, "trail": trail,
            "source_sha256": digest(s["raw"].encode()),
            "target_sha256": digest(target.encode()), "source_ref": source.ref,
            "target_ref": output.ref, "checks": checked, "meaning_reviewed": False}
    repair = None
    if number > 1:
        history = []
        for n, requests in part["requests"].items():
            if s["id"] not in requests:
                continue
            original = groups.get((n, s["id"]))
            result = part["outputs"][n].get(s["id"])
            history.append({"round": n, "input": requests[s["id"]].data,
                            "input_ref": requests[s["id"]].ref,
                            "submitted": original is not None,
                            "request": original.data if original else None,
                            "request_ref": original.ref if original else None,
                            "output": result.data if result else None,
                            "output_ref": result.ref if result else None})
        repair = {**identity, "target_round": number, "history": history,
                  "training_allowed": False, "meaning_reviewed": False}
    return pair, repair, None


def pair_rank(pair):
    """PAIR-BALANCE: seed 0 and exact UTF-8 framing, with no final LF."""
    key = f"0\n{pair['batch']}\n{pair['id']}".encode("utf-8")
    return digest(key), pair["batch"], pair["id"]


def balance(pairs):
    """PAIR-BALANCE: largest-remainder quotas without replacement or bin borrowing."""
    bins = [[], [], []]
    for pair in pairs:
        size = len(pair["input"])
        bins[0 if size < 200 else 1 if size < 500 else 2].append(pair)
    for bucket in bins:
        bucket.sort(key=pair_rank)
    counts = [len(bucket) for bucket in bins]
    maximum = max(n for n in range(len(pairs) + 1)
                  if all(q <= c for q, c in zip(targets(n, WEIGHTS), counts)))
    selections = {}
    for name, size in (("r1", 200), ("r2", maximum)):
        quotas = targets(size, WEIGHTS)
        available = all(q <= c for q, c in zip(quotas, counts))
        chosen = sorted([p for bucket, q in zip(bins, quotas) for p in bucket[:q]],
                        key=pair_rank) if available else []
        ids = [[p["batch"], p["id"]] for p in chosen]
        selections[name] = {"count": size, "bins": quotas, "available": available,
                            "ids": ids, "ordered_ids_sha256": digest(json.dumps(
                                ids, ensure_ascii=True, separators=(",", ":")).encode())}
    r1_ids = {tuple(x) for x in selections["r1"]["ids"]}
    r2_ids = {tuple(x) for x in selections["r2"]["ids"]}
    return {"seed": 0, "weights": WEIGHTS, "pool_bins": counts, "maximum": maximum,
            "selections": selections, "r1_in_r2": r1_ids <= r2_ids,
            "operator_approval_required": True}


def collect(repo=REPO):
    """PAIR-INPUTS through PAIR-BALANCE: verify the fixed two-batch evidence set."""
    evidence = Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    provenance = prompt_inputs(evidence, pins)
    dataset = tomllib.loads(evidence.read("config/datasets.toml").decode())
    revision = dataset["datasets"]["nemotron-swe-v1"]["revision"]
    heldout = set()
    for size in (100, 500):
        entries = evidence.rows(f"var/ste/kit-{size}/source.jsonl",
                                pins[f"kit_{size}_source"])
        heldout.update(entry.data["uuid"] for entry in entries)
    judge = experiment.Judge(PROMPT)
    pairs, repairs, excluded = [], [], []
    seen_rows, seen_uuids = set(), set()
    batch_counts = {}
    for batch in BATCHES:
        root = Path("var/ste") / f"batch-{batch}"
        suffix = batch.replace("-", "_")
        run = evidence.json(root / "run.json")
        require(run["batch"] == batch and run["dataset"] == "nemotron-swe-v1" and
                run["revision"] == revision and run["prompt"] == PROMPT and
                run["prompt_sha256"] == pins["single_text_bundle"],
                f"PAIR-INPUTS: batch provenance differs: {batch}")
        require(run["converted_sha256"] == pins[f"converted_{suffix}"],
                f"PAIR-INPUTS: conversion sha256 differs from run record: {batch}")
        converted = evidence.rows(root / "converted.jsonl", pins[f"converted_{suffix}"])
        require(len(converted) == run["converted"],
                f"PAIR-INPUTS: conversion count differs: {batch}")
        trajectories = {}
        for entry in converted:
            row, origin = entry.data, entry.data["ste"]
            require(origin["batch"] == batch and origin["trajectory"] not in trajectories,
                    f"PAIR-INPUTS: duplicate or changed trajectory: {batch}")
            require(origin["prompt_sha256"] == pins["single_text_bundle"],
                    f"PAIR-PROVENANCE: conversion prompt differs: {batch}")
            require(type(origin["row"]) is int and origin["row"] not in seen_rows,
                    f"PAIR-INPUTS: duplicate dataset row: {origin['row']}")
            require(row["uuid"] not in seen_uuids,
                    f"PAIR-INPUTS: duplicate trajectory UUID: {row['uuid']}")
            require(row["uuid"] not in heldout,
                    f"PAIR-HELDOUT: converted trajectory overlaps a kit: {row['uuid']}")
            seen_rows.add(origin["row"])
            seen_uuids.add(row["uuid"])
            trajectories[origin["trajectory"]] = row
        sources = unique(evidence.rows(root / "source.jsonl", pins[f"source_{suffix}"]))
        codes = unique(evidence.rows(root / "codes.jsonl", pins[f"codes_{suffix}"]))
        require(sources.keys() == codes.keys(), f"PAIR-INPUTS: source/map IDs differ: {batch}")
        parts, owners = load_parts(evidence, batch, pins)
        require(owners.keys() <= sources.keys(), f"PAIR-INPUTS: unknown part ID: {batch}")
        groups = grouped_requests(evidence, batch, parts, pins)
        found_trajectories = set()
        before = len(pairs)
        for key, source in sources.items():
            s = source.data
            if s["traj"] not in trajectories:
                continue
            row = trajectories[s["traj"]]
            require(s["row"] == row["ste"]["row"] and s["uuid"] == row["uuid"],
                    f"PAIR-INPUTS: ambiguous source identity: {batch}/{key}")
            found_trajectories.add(s["traj"])
            if not s["prose"] or s["kind"] not in ("narration", "final"):
                excluded.append({"batch": batch, "id": key, "reason": "no_eligible_target"})
                continue
            require(key in owners, f"PAIR-INPUTS: accepted segment has no part: {batch}/{key}")
            part = parts[owners[key]]
            require(part["meta"]["state"] == "done",
                    f"PAIR-TARGET: assembly could not use unfinished part: {batch}/{key}")
            pair, repair, failure = retain_pair(batch, source, codes[key], row,
                                                part, groups, judge)
            if pair is not None:
                pairs.append(pair)
            if repair is not None:
                repairs.append(repair)
            if failure is not None:
                excluded.append(failure)
        require(found_trajectories == trajectories.keys(),
                f"PAIR-INPUTS: conversion has no source segments: {batch}")
        batch_counts[batch] = {"trajectories": len(trajectories), "pairs": len(pairs) - before}
    pairs.sort(key=pair_rank)
    repairs.sort(key=pair_rank)
    record = {"schema": "weaver-train/rewriter-pairs/v1", "spec": "0.1",
              "commit": mf.git_commit(repo), "source_dataset": "nemotron-swe-v1",
              "source_revision": revision, "inputs": evidence.files,
              "prompt_provenance": provenance, "batches": batch_counts,
              "pairs": len(pairs), "repairs": len(repairs), "excluded": len(excluded),
              "kinds": dict(Counter(p["kind"] for p in pairs)),
              "target_rounds": dict(Counter(p["target_round"] for p in pairs)),
              "heldout_uuid_overlaps": 0, "balance": balance(pairs),
              "limits": {"targets_checker_passed": True, "meaning_reviewed": False,
                         "repair_training_allowed": False}}
    evidence.verify()
    return pairs, repairs, excluded, record


def write_pairs(out_dir, repo=REPO):
    """PAIR-OUTPUT: write a fresh, untracked directory and its sibling manifest."""
    out = Path(out_dir).resolve()
    manifest_path = out.with_name(out.name + ".manifest.json")
    for path in (out, manifest_path):
        if path.is_relative_to(repo.resolve()):
            ignored = subprocess.run(["git", "check-ignore", "--quiet", str(path)], cwd=repo)
            require(ignored.returncode == 0, "PAIR-OUTPUT: output must stay outside git")
    require(not out.exists() and not manifest_path.exists(),
            "PAIR-OUTPUT: output directory or manifest already exists")
    pairs, repairs, excluded, record = collect(repo)
    out.mkdir(parents=True, exist_ok=False)
    for name, rows in (("pairs", pairs), ("repairs", repairs), ("excluded", excluded)):
        with (out / f"{name}.jsonl").open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (out / "pairs-record.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=True) + "\n")
    manifest = mf.build_tree(out, "dataset", "ste-rewriter-pairs",
                             source={"spec": "0.1", "commit": record["commit"]})
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(manifest.to_json())
    return record, manifest


class RenderError(ValueError):
    """RENDER-SEED, RENDER-MASK, RENDER-LENGTH: refuse a different render."""


def seed_system(folded: bytes, saved: bytes, expected_sha256: str) -> str:
    """RENDER-SEED: compare already extracted saved bytes to the pinned data bytes.

    This byte check is not a loop record reader or evidence of a real seed. The
    future build entry point must obtain saved bytes through the agreed record.
    """
    if not isinstance(folded, bytes) or not isinstance(saved, bytes):
        raise RenderError("RENDER-SEED: folded and saved system bytes are required")
    if digest(folded) != expected_sha256:
        raise RenderError("RENDER-SEED: folded prompt sha256 differs")
    if saved != folded:
        raise RenderError("RENDER-SEED: saved system bytes differ from folded prompt")
    try:
        return saved.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError("RENDER-SEED: system bytes are not UTF-8") from exc


def initial_messages(system: str, masked_input: str) -> list[dict]:
    """RENDER-LAYOUT: preserve the folded system and the exact one-user wrapper."""
    return [{"role": "system", "content": system},
            {"role": "user", "content": "<<<TEXT\n" + masked_input + "\nTEXT>>>"}]


def render_pair(tokenizer, pair: dict, system: str) -> dict:
    """RENDER-LAYOUT, RENDER-MASK, RENDER-LENGTH: render one checked pair in memory.

    The caller supplies verified folded system text. This function neither checks
    seed provenance nor writes a training data record. Examples stay in the system.
    """
    messages = initial_messages(system, pair["input"])
    message_bytes = json.dumps(messages, ensure_ascii=True,
                               separators=(",", ":")).encode("utf-8")
    options = {"enable_thinking": False, "tokenize": True, "return_dict": False}
    prefix = tokenizer.apply_chat_template(messages, add_generation_prompt=True,
                                            **options)
    complete = messages + [{"role": "assistant", "content": pair["target"]}]
    ids = tokenizer.apply_chat_template(complete, add_generation_prompt=False,
                                         **options)
    identity = f"{pair['batch']}/{pair['id']}"
    if ids[:len(prefix)] != prefix or len(ids) <= len(prefix):
        raise RenderError(f"RENDER-MASK: final target prefix differs or is empty: {identity}")
    if len(ids) > MAX_SEQ_LENGTH:
        raise RenderError(f"RENDER-LENGTH: {identity} has {len(ids)} tokens, "
                          f"limit {MAX_SEQ_LENGTH}")
    # RENDER-OUTPUT: keep pair identity and complete initial-list identity per row.
    fields = ("batch", "id", "trajectory", "uuid", "row", "kind", "loc", "target_round")
    return {**{key: pair[key] for key in fields}, "input_ids": ids,
            "assistant_masks": [0] * len(prefix) + [1] * (len(ids) - len(prefix)),
            "initial_messages_sha256": digest(message_bytes)}


def main(argv=None):
    """PAIR-OUTPUT, REWRITE-FILES: expose pair collection without a training path."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pairs = commands.add_parser("pairs", help="verify and retain the fixed batch pairs")
    pairs.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        record, manifest = write_pairs(args.out)
    except (PairError, OSError, KeyError, TypeError, IndexError, ValueError) as exc:
        print(f"PAIR: stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"pairs": record["pairs"], "repairs": record["repairs"],
                      "excluded": record["excluded"], "bins": record["balance"]["pool_bins"],
                      "maximum": record["balance"]["maximum"],
                      "tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
