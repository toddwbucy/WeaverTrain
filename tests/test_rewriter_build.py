"""Spec 0.1 section 9: PAIR integrity, retained repairs and exact selection."""

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import rewriter_build as build


def save(path, value, rows=False):
    """PAIR-INPUTS: write synthetic test evidence, never production training text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(x) + "\n" for x in value) if rows else json.dumps(value)
    path.write_text(text)


def pin(world, key, relative):
    """PAIR-INPUTS: re-pin a fixture to reach checks after the digest boundary."""
    world.pins[key] = build.mf.sha256_file(world.repo / relative)
    write_pins(world)


def write_pins(world):
    """PAIR-INPUTS: fixture pins replace only the temporary repository's pins."""
    path = world.repo / "config/rewriter-inputs.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f'{key} = "{value}"\n' for key, value in world.pins.items()))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """PAIR clauses: two synthetic batches, with a four-round repair history."""
    repo = tmp_path / "repo"
    repo.mkdir()
    w = SimpleNamespace(repo=repo, pins={})
    save(repo / "var/ste/approved-verbs.json", {})
    pin(w, "approved_verbs", "var/ste/approved-verbs.json")
    prompt = {"system": "Rules.", "examples": [], "meta": {
        "verbs_sha256": w.pins["approved_verbs"],
        "limit_instruction": 20, "limit_description": 25}}
    w.pins["system_text"] = build.digest(prompt["system"].encode())
    w.pins["single_text_bundle"] = build.digest(json.dumps(["Rules.", []]).encode())
    w.pins["grouped_instruction"] = build.digest(b"Grouped rules.")
    prompt["meta"]["prompt_sha256"] = w.pins["single_text_bundle"]
    save(repo / "var/ste" / build.PROMPT, prompt)
    for name in ("ste_check.py", "ste_convert.py", "ste_experiment.py"):
        path = repo / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Fixture identity.\n")
    (repo / "config/datasets.toml").write_text(
        '[datasets.nemotron-swe-v1]\nrevision = "fixture-revision"\n')
    for size in (100, 500):
        relative = f"var/ste/kit-{size}/source.jsonl"
        save(repo / relative, [{"uuid": "held-out", "row": 999}], rows=True)
        pin(w, f"kit_{size}_source", relative)
    for row_number, batch in enumerate(build.BATCHES):
        root = repo / "var/ste" / f"batch-{batch}"
        part = repo / "experiments" / f"ste-batch-{batch}" / "p001"
        sources, codes, inputs = [], [], []
        for index, (kind, raw, loc) in enumerate((
            ("narration", "  Check `item`.\n", {"msg": 0, "field": "content"}),
            ("final", "Done.", {"msg": 1, "field": "finish", "call": 0}),
        ), 1):
            key = f"T001.{index:03d}"
            lead, body, trail = build.ste_convert.split_ws(raw)
            masked, mapping = build.ste_convert.mask(body)
            sources.append({"id": key, "traj": "T001", "row": row_number,
                            "uuid": f"u{row_number}", "kind": kind, "raw": raw,
                            "loc": loc, "prose": True})
            codes.append({"id": key, "lead": lead, "trail": trail, "codes": mapping})
            inputs.append({"id": key, "kind": kind, "text": masked})
        converted = {"uuid": f"u{row_number}", "ste": {
            "batch": batch, "trajectory": "T001", "row": row_number,
            "prompt_sha256": w.pins["single_text_bundle"]}, "messages": [
                {"role": "assistant", "content": "  Use `item`.\n"},
                {"role": "assistant", "tool_calls": [{"function": {
                    "name": "finish", "arguments": json.dumps({"message": "Done."})}}]}]}
        suffix = batch.replace("-", "_")
        for name, values in (("source", sources), ("codes", codes),
                             ("converted", [converted])):
            save(root / f"{name}.jsonl", values, rows=True)
            pin(w, f"{name}_{suffix}", f"var/ste/batch-{batch}/{name}.jsonl")
        save(root / "run.json", {"batch": batch, "dataset": "nemotron-swe-v1",
             "revision": "fixture-revision", "prompt": build.PROMPT,
             "prompt_sha256": w.pins["single_text_bundle"], "converted": 1,
             "converted_sha256": w.pins[f"converted_{suffix}"]})
        save(part / "input.jsonl", inputs, rows=True)
        save(part / "part.json", {"prompt": build.PROMPT, "state": "done",
             "prompt_sha256": w.pins["single_text_bundle"], "records": 2,
             "input_sha256": build.mf.sha256_file(part / "input.jsonl")})
        previous = ""
        final_round = 4 if batch == "200" else 1
        for number in range(1, final_round + 1):
            requests = inputs if number == 1 else [{**inputs[0], "previous": previous,
                                                    "problems": "Keep the placeholder."}]
            if number > 1:
                save(part / f"repair-{number - 1}.jsonl", requests, rows=True)
            target = "Use [[C1]]." if number == final_round else f"Use [[C{number + 1}]]."
            outputs = [{"id": inputs[0]["id"], "output": target}]
            if number == 1:
                outputs.append({"id": inputs[1]["id"], "output": "Done."})
            save(part / f"round{number}.jsonl", outputs, rows=True)
            original = {"key": "p001/T001.001", "request": {
                "systemInstruction": {"parts": [{"text": "Grouped rules."}]},
                "contents": [{"role": "user", "parts": [
                    {"text": f"Original grouped request {number}."}]}]}}
            request_path = root / "gemini" / f"round{number}.jsonl"
            save(request_path, [original], rows=True)
            save(request_path.with_suffix(".json"), {
                "name": batch, "round": number, "prompt": build.PROMPT, "group": 20,
                "records": len(requests), "requests": 1,
                "input_sha256": build.mf.sha256_file(request_path)})
            previous = target
    write_pins(w)
    lex = build.ste_check.Lexicon.build(
        {"use": ["v"], "done": ["adj"]}, {}, {}, [], [], ["use"])
    monkeypatch.setattr(build.experiment, "REPO", repo)
    monkeypatch.setattr(build.ste_check.Lexicon, "load", classmethod(lambda cls: lex))
    return w


def test_pair_input_pins_match_the_merged_spec():
    """PAIR-INPUTS, PAIR-PROVENANCE: config cannot drift from section 11."""
    pins = tomllib.loads((REPO / "config/rewriter-inputs.toml").read_text())
    spec = (REPO / "docs/lora-rewriter-Spec.md").read_text()
    for key, value in pins.items():
        assert f"{key}\n{value}\n" in spec


def test_pair_target_and_repair_records_preserve_original_evidence(world):
    """PAIR-TARGET, PAIR-REPAIRS, PAIR-PROVENANCE: exact target and full history."""
    pairs, repairs, excluded, record = build.collect(world.repo)
    assert len(pairs) == 4 and len(repairs) == 1 and not excluded
    pair = next(p for p in pairs if p["batch"] == "200" and p["kind"] == "narration")
    assert pair["input"] == "Check [[C1]]." and pair["target"] == "Use [[C1]]."
    assert pair["target_round"] == 4 and pair["lead"] == "  " and pair["trail"] == "\n"
    assert pair["codes"] == ["`item`"] and not pair["meaning_reviewed"]
    history = repairs[0]["history"]
    assert [r["round"] for r in history] == [1, 2, 3, 4]
    assert history[1]["input"]["previous"] == history[0]["output"]["output"]
    assert history[1]["input"]["problems"] == "Keep the placeholder."
    for step in history:
        saved = json.loads((world.repo / step["request_ref"]["file"]).read_text())
        assert step["request"] == saved
    assert not repairs[0]["training_allowed"]
    assert record["prompt_provenance"] == {k: world.pins[k] for k in build.PROVENANCE}
    assert record["source_revision"] == "fixture-revision"
    assert record["balance"]["selections"]["r1"]["available"] is False


@pytest.mark.parametrize("relative", [
    "var/ste/batch-200/converted.jsonl", "var/ste/batch-200/source.jsonl",
    "var/ste/batch-200/codes.jsonl", "var/ste/kit-100/source.jsonl",
    "var/ste/kit-500/source.jsonl", "experiments/ste-batch-200/p001/input.jsonl",
    "var/ste/batch-200/gemini/round1.jsonl",
])
def test_pair_inputs_reject_changed_file(world, relative):
    """PAIR-INPUTS, PAIR-HELDOUT: even valid JSON must match its digest."""
    path = world.repo / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(build.PairError, match="sha256"):
        build.collect(world.repo)


@pytest.mark.parametrize("missing", [False, True])
def test_pair_target_checks_dictionary_before_judge(world, monkeypatch, missing):
    """PAIR-TARGET: absent and changed dictionaries stop before Judge reads them."""
    path = world.repo / "var/ste/approved-verbs.json"
    path.unlink() if missing else path.write_text('{"changed": true}')
    def forbidden(*args):
        """PAIR-TARGET: invalid dictionary bytes must stop before this point."""
        raise AssertionError("Judge must not be constructed")
    monkeypatch.setattr(build.experiment, "Judge", forbidden)
    with pytest.raises((build.PairError, FileNotFoundError)):
        build.collect(world.repo)


def change_conversion(world, modify):
    """PAIR-INPUTS: keep digests valid while changing a fixture's identity."""
    relative = "var/ste/batch-200-2/converted.jsonl"
    row = json.loads((world.repo / relative).read_text())
    modify(row)
    save(world.repo / relative, [row], rows=True)
    pin(world, "converted_200_2", relative)
    path = world.repo / "var/ste/batch-200-2/run.json"
    run = json.loads(path.read_text())
    run["converted_sha256"] = world.pins["converted_200_2"]
    save(path, run)


def test_pair_inputs_reject_duplicate_dataset_rows(world):
    """PAIR-INPUTS: different batches cannot reuse the same dataset row."""
    change_conversion(world, lambda row: row["ste"].update(row=0))
    with pytest.raises(build.PairError, match="duplicate dataset row"):
        build.collect(world.repo)


def test_pair_heldout_compares_uuid_not_dataset_row(world):
    """PAIR-HELDOUT: the kit UUID stops a conversion on a different row."""
    change_conversion(world, lambda row: row.update(uuid="held-out"))
    with pytest.raises(build.PairError, match="PAIR-HELDOUT"):
        build.collect(world.repo)


@pytest.mark.parametrize("name", ["source", "codes"])
def test_pair_inputs_reject_duplicate_segment_ids(world, name):
    """PAIR-INPUTS: duplicates cannot silently replace source or code-map entries."""
    relative = f"var/ste/batch-200/{name}.jsonl"
    path = world.repo / relative
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    pin(world, f"{name}_200", relative)
    with pytest.raises(build.PairError, match="duplicate or invalid id"):
        build.collect(world.repo)


def test_pair_target_rejects_last_output_without_earlier_pass_fallback(world):
    """PAIR-TARGET: a later bad output overrides an earlier passing output."""
    part = world.repo / "experiments/ste-batch-200/p001"
    save(part / "round3.jsonl", [{"id": "T001.001", "output": "Use [[C1]]."}], rows=True)
    request = json.loads((part / "repair-3.jsonl").read_text())
    request["previous"] = "Use [[C1]]."
    save(part / "repair-3.jsonl", [request], rows=True)
    save(part / "round4.jsonl", [{"id": "T001.001", "output": "Use [[C9]]."}], rows=True)
    pairs, repairs, excluded, _ = build.collect(world.repo)
    assert len(pairs) == 3 and not repairs
    assert excluded[0]["reason"] == "target_check" and excluded[0]["round"] == 4


def test_pair_target_excludes_bad_source_map(world):
    """PAIR-TARGET: an intact file digest does not replace the mask/map check."""
    relative = "var/ste/batch-200/codes.jsonl"
    path = world.repo / relative
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["codes"] = ["`other`"]
    save(path, rows, rows=True)
    pin(world, "codes_200", relative)
    pairs, _, excluded, _ = build.collect(world.repo)
    assert len(pairs) == 3 and excluded[0]["reason"] == "source_mask_or_map"


def test_pair_target_requires_exact_restored_conversion(world):
    """PAIR-TARGET: final restoration includes the original outer whitespace."""
    change_conversion(world, lambda row: row["messages"][0].update(content="Use `item`."))
    pairs, _, excluded, _ = build.collect(world.repo)
    assert len(pairs) == 3 and excluded[0]["reason"] == "restored_target"


def test_pair_repairs_rejects_changed_previous_output(world):
    """PAIR-REPAIRS: keep the previous output as recorded, without reconstruction."""
    path = world.repo / "experiments/ste-batch-200/p001/repair-1.jsonl"
    row = json.loads(path.read_text())
    row["previous"] = "A different output."
    save(path, [row], rows=True)
    with pytest.raises(build.PairError, match="previous output differs"):
        build.collect(world.repo)


def test_pair_provenance_rejects_changed_grouped_instruction(world):
    """PAIR-PROVENANCE: source bundle and grouped instruction are distinct pins."""
    path = world.repo / "var/ste/batch-200/gemini/round1.jsonl"
    row = json.loads(path.read_text())
    row["request"]["systemInstruction"]["parts"][0]["text"] = "Rules."
    save(path, [row], rows=True)
    state = json.loads(path.with_suffix(".json").read_text())
    state["input_sha256"] = build.mf.sha256_file(path)
    save(path.with_suffix(".json"), state)
    with pytest.raises(build.PairError, match="grouped instruction differs"):
        build.collect(world.repo)


def test_pair_repairs_retains_unsubmitted_empty_round(world):
    """PAIR-REPAIRS: later notes in an empty round cannot fabricate a request."""
    part = world.repo / "experiments/ste-batch-200/p001"
    save(part / "round3.jsonl", [], rows=True)
    repair = json.loads((part / "repair-3.jsonl").read_text())
    repair["previous"] = "Use [[C3]]."
    save(part / "repair-3.jsonl", [repair], rows=True)
    request = world.repo / "var/ste/batch-200/gemini/round3.jsonl"
    save(request, [], rows=True)
    state = json.loads(request.with_suffix(".json").read_text())
    state.update(records=0, requests=0, input_sha256=build.mf.sha256_file(request))
    save(request.with_suffix(".json"), state)
    pairs, repairs, excluded, _ = build.collect(world.repo)
    assert len(pairs) == 4 and not excluded
    empty = repairs[0]["history"][2]
    assert empty["round"] == 3 and not empty["submitted"]
    assert empty["request"] is None and empty["output"] is None
    assert empty["input"]["problems"] == "Keep the placeholder."


def test_pair_repairs_missing_request_for_output_still_stops(world):
    """PAIR-REPAIRS: the empty-round case cannot excuse missing request evidence."""
    request = world.repo / "var/ste/batch-200/gemini/round3.jsonl"
    save(request, [], rows=True)
    state = json.loads(request.with_suffix(".json").read_text())
    state.update(records=0, requests=0, input_sha256=build.mf.sha256_file(request))
    save(request.with_suffix(".json"), state)
    with pytest.raises(build.PairError, match="output has no original request"):
        build.collect(world.repo)


def test_pair_balance_exact_counts_order_and_nested_sets():
    """PAIR-BALANCE: the ruled pool fits 3383, never 3384, with nested R1 IDs."""
    pool = [{"batch": "200", "id": f"b{bin_id}-{i}", "input": "x" * size}
            for bin_id, (count, size) in enumerate(((10401, 199), (1852, 200), (1116, 500)))
            for i in range(count)]
    result = build.balance(pool)
    assert result == build.balance(list(reversed(pool)))
    assert result["maximum"] == 3383 and result["r1_in_r2"]
    assert result["selections"]["r1"]["bins"] == [74, 60, 66]
    assert result["selections"]["r2"]["bins"] == [1252, 1015, 1116]
    assert build.targets(3384, [37, 30, 33])[-1] == 1117
    assert build.targets(50, [37, 30, 33]) == [19, 15, 16]
    ids = result["selections"]["r2"]["ids"]
    assert len({tuple(x) for x in ids}) == 3383
    expected = sorted(pool, key=lambda p: (
        hashlib.sha256(("0\n200\n" + p["id"]).encode()).hexdigest(), "200", p["id"]))
    selected = {tuple(x) for x in ids}
    assert ids == [[p["batch"], p["id"]] for p in expected if (p["batch"], p["id"]) in selected]


def test_pair_balance_does_not_borrow_from_another_bin():
    """PAIR-BALANCE: a reduced long bin cannot be replaced with short pairs."""
    result = build.balance([{"batch": "200", "id": str(i), "input": "x"} for i in range(300)])
    assert result["maximum"] == 1
    assert not result["selections"]["r1"]["available"]
    assert result["selections"]["r1"]["ids"] == []
    assert result["operator_approval_required"]


def test_pair_output_manifest_and_no_overwrite(world, tmp_path):
    """PAIR-OUTPUT: all retained files enter the manifest and cannot be replaced."""
    out = tmp_path / "pairs"
    record, manifest = build.write_pairs(out, world.repo)
    assert record["pairs"] == 4
    assert not build.mf.verify_tree(manifest, out)
    assert {p.name for p in out.iterdir()} == {
        "pairs.jsonl", "repairs.jsonl", "excluded.jsonl", "pairs-record.json"}
    before = (out / "pairs-record.json").read_bytes()
    with pytest.raises(build.PairError, match="already exists"):
        build.write_pairs(out, world.repo)
    assert (out / "pairs-record.json").read_bytes() == before


def test_pair_target_checks_glued_literals_even_when_placeholders_pass(world):
    """PAIR-TARGET: a retained placeholder cannot excuse a changed adjacent literal."""
    source = {"id": "a", "traj": "T1", "uuid": "u1", "row": 0,
              "kind": "narration", "loc": {"msg": 0, "field": "content"},
              "raw": "Use v1/`item`."}
    masked, codes = build.ste_convert.mask(source["raw"])
    entry = build.Entry({"id": "a", "kind": "narration", "text": masked}, {})
    target = "Use [[C1]][[C2]]."
    part = {"inputs": {"a": entry}, "outputs": {
        1: {"a": build.Entry({"id": "a", "output": target}, {})}}}
    code = build.Entry({"id": "a", "lead": "", "trail": "", "codes": codes}, {})
    row = {"messages": [{"role": "assistant", "content": "Use `item`v1/."}]}
    pair, _, failure = build.retain_pair("200", build.Entry(source, {}), code,
                                         row, part, {}, build.experiment.Judge(build.PROMPT))
    assert pair is None and failure["reason"] == "target_check"
    assert failure["checks"]["ok"]
    assert failure["altered_literals"]


def test_pair_inputs_detect_changes_after_read(world):
    """PAIR-INPUTS: a changed input cannot be published under its earlier digest."""
    evidence = build.Evidence(world.repo)
    evidence.read("var/ste/approved-verbs.json")
    (world.repo / "var/ste/approved-verbs.json").write_text("changed")
    with pytest.raises(build.PairError, match="file changed during collection"):
        evidence.verify()


def test_pair_inputs_collect_rejects_a_change_after_loading(world, monkeypatch):
    """PAIR-INPUTS: collection must invoke its final input check before output."""
    original = build.retain_pair
    path = world.repo / "experiments/ste-batch-200/p001/part.json"

    def change_loaded_file(*args):
        """PAIR-INPUTS: simulate a concurrent update after the part was checked."""
        result = original(*args)
        path.write_text('{"changed_after_read": true}')
        return result

    monkeypatch.setattr(build, "retain_pair", change_loaded_file)
    with pytest.raises(build.PairError, match="file changed during collection"):
        build.collect(world.repo)


def test_pair_inputs_second_read_rejects_different_bytes(world):
    """PAIR-INPUTS: a second read cannot replace the first file identity."""
    evidence = build.Evidence(world.repo)
    relative = "var/ste/approved-verbs.json"
    evidence.read(relative)
    (world.repo / relative).write_text('{"changed": true}')
    with pytest.raises(build.PairError, match="file changed during collection"):
        evidence.read(relative)


def test_pair_output_manifest_must_also_stay_outside_git(world, monkeypatch):
    """PAIR-OUTPUT: an ignored directory does not imply its sibling is ignored."""
    out = world.repo / "ignored-directory"
    monkeypatch.setattr(build.subprocess, "run", lambda argv, **kwargs:
                        SimpleNamespace(returncode=int(argv[-1] != str(out))))
    with pytest.raises(build.PairError, match="outside git"):
        build.write_pairs(out, world.repo)
    assert not out.exists()
