"""Spec 0.2 PAIR and RENDER clauses: collect pairs and build final-target masks.

The pairs and build commands read local evidence only. The build verifies the
approved folded file without requiring a loop seed record, under RENDER-SEED.
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
# REWRITE-ORDER, RENDER-OUTPUT: building a selection does not start its training.
PROCEDURES = {"r1": 200, "r2": 3383}
MODEL = "nemotron-3-nano-30b-a3b"


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


def folded_system(folded: bytes, expected_sha256: str) -> str:
    """RENDER-SEED: check local prompt bytes without claiming loop seed evidence.

    REWRITE-RECONCILE checks the seed record separately before loop use.
    """
    if not isinstance(folded, bytes):
        raise RenderError("RENDER-SEED: folded system bytes are required")
    if digest(folded) != expected_sha256:
        raise RenderError("RENDER-SEED: folded prompt sha256 differs")
    try:
        return folded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError("RENDER-SEED: system bytes are not UTF-8") from exc


def initial_messages(system: str, masked_input: str) -> list[dict]:
    """RENDER-LAYOUT: preserve the folded system and the exact one-user wrapper."""
    return [{"role": "system", "content": system},
            {"role": "user", "content": "<<<TEXT\n" + masked_input + "\nTEXT>>>"}]


def render_pair(tokenizer, pair: dict, system: str, *, model=MODEL) -> dict:
    """RENDER-LAYOUT, RENDER-MASK, RENDER-LENGTH: render one checked pair in memory.

    The caller supplies verified folded system text. This function neither checks
    seed provenance nor writes a training data record. Examples stay in the system.
    """
    messages = initial_messages(system, pair["input"])
    if model != MODEL:
        import rewriter_granite_profile as granite

        require(model == granite.MODEL, "GRANITE-MODEL: unknown rendering model")
        granite.verify_render(tokenizer, messages, pair["target"])
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


def read_pair_directory(directory, evidence, pins):
    """RENDER-LAYOUT, RENDER-OUTPUT: verify the complete pair tree before selection."""
    directory = Path(directory).resolve()
    manifest_path = directory.with_name(directory.name + ".manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = mf.TreeManifest.from_dict(json.loads(manifest_bytes))
    require(manifest.kind == "dataset" and manifest.name == "ste-rewriter-pairs",
            "RENDER-OUTPUT: expected a pair dataset manifest")
    mf.require_tree(manifest, directory)
    names = {"pairs.jsonl", "repairs.jsonl", "excluded.jsonl", "pairs-record.json"}
    require({entry.path for entry in manifest.files} == names,
            "RENDER-OUTPUT: pair directory file set differs")
    captured = {}
    for entry in manifest.files:
        raw = (directory / entry.path).read_bytes()
        require(digest(raw) == entry.sha256,
                f"RENDER-OUTPUT: captured pair file differs: {entry.path}")
        captured[entry.path] = raw
    record = json.loads(captured["pairs-record.json"])
    require(record["schema"] == "weaver-train/rewriter-pairs/v1",
            "RENDER-OUTPUT: pair record schema differs")
    rows = {name: [json.loads(line) for line in captured[name + ".jsonl"].splitlines()
                   if line.strip()] for name in ("pairs", "repairs", "excluded")}
    require(all(record[name] == len(values) for name, values in rows.items()),
            "RENDER-OUTPUT: pair record counts differ")
    require(record["limits"] == {"targets_checker_passed": True,
                                  "meaning_reviewed": False,
                                  "repair_training_allowed": False},
            "PAIR-TARGET, PAIR-REPAIRS: pair record limits differ")
    require(record["heldout_uuid_overlaps"] == 0,
            "PAIR-HELDOUT: pair record contains held-out overlap")
    provenance = prompt_inputs(evidence, pins)
    require(record["prompt_provenance"] == provenance,
            "PAIR-PROVENANCE: pair source prompt identities differ")
    dataset = tomllib.loads(evidence.read("config/datasets.toml").decode())
    require(record["source_dataset"] == "nemotron-swe-v1" and
            record["source_revision"] == dataset["datasets"]["nemotron-swe-v1"][
                "revision"], "PAIR-INPUTS: pair dataset revision differs")
    heldout = set()
    for size in (100, 500):
        path = f"var/ste/kit-{size}/source.jsonl"
        heldout.update(entry.data["uuid"] for entry in
                       evidence.rows(path, pins[f"kit_{size}_source"]))
        require(record["inputs"][path]["sha256"] == pins[f"kit_{size}_source"],
                "PAIR-HELDOUT: pair kit identity differs")
    for batch in BATCHES:
        for name in ("converted", "source", "codes"):
            path = f"var/ste/batch-{batch}/{name}.jsonl"
            require(record["inputs"][path]["sha256"] ==
                    pins[f"{name}_{batch.replace('-', '_')}"],
                    "PAIR-INPUTS: pair source pin differs")
    indexed = {}
    for pair in rows["pairs"]:
        key = (pair["batch"], pair["id"])
        require(key not in indexed and pair["batch"] in BATCHES and pair["id"],
                "PAIR-INPUTS: duplicate or unknown pair identity")
        require(pair["uuid"] not in heldout,
                "PAIR-HELDOUT: pair trajectory overlaps a kit")
        require(pair["checks"]["ok"] and pair["meaning_reviewed"] is False,
                "PAIR-TARGET: pair checks or review limit differ")
        require(digest(pair["target"].encode()) == pair["target_sha256"],
                "PAIR-TARGET: pair target hash differs")
        original = pair["lead"] + ste_convert.unmask(pair["input"], pair["codes"])
        original += pair["trail"]
        require(digest(original.encode()) == pair["source_sha256"],
                "PAIR-TARGET: pair source hash differs")
        indexed[key] = pair
    measured = balance(rows["pairs"])
    require(record["balance"] == measured,
            "PAIR-BALANCE: stored selection differs from measured pairs")
    return indexed, rows["excluded"], record, manifest, digest(manifest_bytes)


def build_tokenizer(evidence, model=MODEL):
    """REWRITE-LIMITS, RENDER-OUTPUT: verify the pinned model before tokenizer use."""
    from weaver_train import loader

    configured = tomllib.loads(evidence.read("config/models.toml").decode())["models"][
        model]
    model_manifest = mf.load_tree(loader.manifest_path_for(model))
    require(model_manifest.kind == "model" and model_manifest.name == model and
            all(model_manifest.source.get(key) == configured[key]
                for key in ("repo_id", "revision")),
            "REWRITE-LIMITS: model manifest differs from configured reference")
    model_dir = loader.resolve_dir(model_manifest)
    model_hash = mf.require_tree(model_manifest, model_dir)
    if model != MODEL:
        import rewriter_granite_profile as granite

        require(model == granite.MODEL, "GRANITE-MODEL: unknown tokenizer model")
        granite.verify_model_files(model_manifest, model_dir)
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        model_dir, local_files_only=True, trust_remote_code=False)
    if model != MODEL:
        granite.verify_tokenizer(tokenizer)
    return tokenizer, model_manifest, model_dir, model_hash


def fresh_build_output(out_dir, repo):
    """RENDER-OUTPUT: refuse an existing destination or a path exposed to git."""
    out = Path(out_dir).resolve()
    manifest_path = out.with_name(out.name + ".manifest.json")
    for path in (out, manifest_path):
        if path.is_relative_to(repo.resolve()):
            ignored = subprocess.run(["git", "check-ignore", "--quiet", str(path)],
                                     cwd=repo)
            require(ignored.returncode == 0,
                    "RENDER-OUTPUT: output must stay outside git")
    require(not out.exists() and not manifest_path.exists(),
            "RENDER-OUTPUT: output directory or manifest already exists")
    return out, manifest_path


def write_build(pair_dir, folded_path, procedure, out_dir, *, repo=REPO):
    """RENDER clauses: write the approved selection, masks and immutable identities."""
    import rewriter_granite_profile as granite

    dense = procedure == granite.R1
    require(procedure in (*PROCEDURES, "r1-e4", granite.R1), "RENDER-OUTPUT: unknown procedure selection")
    # REWRITE-R1-E4: reuse the approved R1 selection without changing the pair pool.
    selection_name = "r1" if procedure == "r1-e4" or dense else procedure
    if procedure == "r1-e4" or dense:
        commit = mf.git_commit(repo)
        require(isinstance(commit["rev"], str) and bool(commit["rev"]) and
                commit["dirty"] is False,
                "REWRITE-R1-E4: a known clean checkout is required for the training build")
    out, manifest_path = fresh_build_output(out_dir, repo)
    pair_root = Path(pair_dir).resolve()
    require(out != pair_root and pair_root not in out.parents and
            out not in pair_root.parents,
            "RENDER-OUTPUT: output overlaps the pair directory")
    evidence = Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    folded = Path(folded_path).read_bytes()
    system = folded_system(folded, pins["folded_prompt"])
    indexed, removed, pairs, pair_manifest, pair_manifest_sha = read_pair_directory(
        pair_root, evidence, pins)
    selection = pairs["balance"]["selections"][selection_name]
    require(selection["available"] and selection["count"] == PROCEDURES[selection_name]
            and selection["ordered_ids_sha256"] == pins[f"{selection_name}_ordered_ids"],
            "PAIR-BALANCE: selection needs an operator decision")
    if dense:
        import rewriter_provenance as provenance

        imports = provenance.capture(repo)
    tokenizer, model_manifest, model_dir, model_hash = (
        build_tokenizer(evidence, granite.MODEL) if dense else build_tokenizer(evidence))
    # REWRITE-LIMITS: never create build files inside or around the reference tree.
    model_root = model_dir.resolve()
    require(out != model_root and model_root not in out.parents and
            out not in model_root.parents,
            "REWRITE-LIMITS: output overlaps the reference model directory")
    template_hash = digest(tokenizer.get_chat_template().encode("utf-8"))
    record = {
        "schema": "weaver-train/rewriter-build/v1", "spec": "0.2",
        "status": "started", "commit": mf.git_commit(repo), "procedure": procedure,
        "tree_hash": model_hash, "template_hash": template_hash, "tools": False,
        "enable_thinking": False, "max_seq_length": MAX_SEQ_LENGTH,
        "render_only": False, "seed": 0, "selection": selection,
        "pair_manifest": json.loads(pair_manifest.to_json()),
        "pair_manifest_sha256": pair_manifest_sha,
        "source_dataset": pairs["source_dataset"],
        "source_revision": pairs["source_revision"], "inputs": evidence.files,
        "folded_prompt": digest(folded),
        "prompt_provenance": pairs["prompt_provenance"],
        "limits": pairs["limits"], "removed": removed,
        "loop_checks": {"seed_check_complete": False,
                        "joint_token_check_complete": False},
        "completed_ids": [], "counts": {"pairs": 0, "all_tokens": 0,
                                         "final_target_tokens": 0},
    }
    if dense:
        record.update(**granite.IDENTITY, imports_before=imports)
    # RENDER-OUTPUT: reserve the directory once. A failed build keeps its partial rows.
    out.mkdir(parents=True, exist_ok=False)

    def save_record():
        """REWRITE-RECORD: update only this invocation's retained build status."""
        (out / "build.json").write_text(json.dumps(record, indent=2, ensure_ascii=True)
                                        + "\n", encoding="utf-8")

    save_record()
    try:
        with (out / "train.jsonl").open("x", encoding="utf-8") as stream:
            for key in selection["ids"]:
                row = (render_pair(tokenizer, indexed[tuple(key)], system, model=granite.MODEL)
                       if dense else render_pair(tokenizer, indexed[tuple(key)], system))
                stream.write(json.dumps(row, ensure_ascii=True) + "\n")
                record["completed_ids"].append(key)
                record["counts"]["pairs"] += 1
                record["counts"]["all_tokens"] += len(row["input_ids"])
                record["counts"]["final_target_tokens"] += sum(row["assistant_masks"])
        # RENDER-OUTPUT: reject input changes before sealing a successful build.
        evidence.verify()
        require(Path(folded_path).read_bytes() == folded,
                "RENDER-SEED: folded file changed during build")
        mf.require_tree(pair_manifest, pair_root)
        require(mf.sha256_file(pair_root.with_name(
            pair_root.name + ".manifest.json")) == pair_manifest_sha,
            "RENDER-OUTPUT: pair manifest changed during build")
        require(digest(tokenizer.get_chat_template().encode()) == template_hash,
                "RENDER-LAYOUT: tokenizer template changed during build")
        mf.require_tree(model_manifest, model_dir)
        epoch_numbers = (1, 2, 3, 4) if procedure == "r1-e4" or dense else (1, 2)
        record["tokens_per_epoch"] = [
            {"epoch": epoch, **record["counts"]} for epoch in epoch_numbers]
        if procedure == "r1-e4" or dense:
            require(mf.git_commit(repo) == commit,
                    "REWRITE-R1-E4: checkout changed during the training build")
        if dense:
            record["imports_after"] = provenance.capture(repo, previous=imports)
        record["status"] = "completed"
        save_record()
        manifest = mf.build_tree(out, "dataset", "ste-rewriter-build",
                                 source={"spec": "0.2", "commit": record["commit"]})
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(manifest.to_json())
    except BaseException as exc:
        record["status"] = "failed"
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        save_record()
        raise
    return record, manifest


def main(argv=None, *, repo=REPO):
    """PAIR-OUTPUT, RENDER-OUTPUT: expose collection and verified local rendering."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pairs = commands.add_parser("pairs", help="verify and retain the fixed batch pairs")
    pairs.add_argument("--out", required=True)
    build = commands.add_parser("build", help="render an approved pair selection")
    build.add_argument("--pairs", type=Path, required=True)
    build.add_argument("--folded-prompt", type=Path, required=True)
    build.add_argument("--selection", choices=(*PROCEDURES, "r1-e4", "granite-r1"), required=True)
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "pairs":
            record, manifest = write_pairs(args.out, repo)
            result = {"pairs": record["pairs"], "repairs": record["repairs"],
                      "excluded": record["excluded"],
                      "bins": record["balance"]["pool_bins"],
                      "maximum": record["balance"]["maximum"]}
        else:
            record, manifest = write_build(args.pairs, args.folded_prompt,
                                           args.selection, args.out, repo=repo)
            result = {"selection": args.selection, "status": record["status"],
                      "counts": record["counts"]}
    except (mf.ManifestError, OSError, KeyError, TypeError,
            IndexError, ValueError) as exc:
        print(f"{args.command}: stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({**result, "tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
