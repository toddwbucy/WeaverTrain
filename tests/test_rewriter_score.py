"""Spec 0.1 section 9: frozen kit, prompt fit and first-output checker records."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_score as score


def save_rows(path, rows):
    """REWRITE-KIT: write synthetic test inputs, never production training data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(score.jsonl_bytes(rows))


def save_pins(world):
    """REWRITE-KIT, REWRITE-CHECK: pin the temporary fixture's source bytes."""
    for size in (100, 500):
        path = world.repo / f"var/ste/kit-{size}/source.jsonl"
        world.pins[f"kit_{size}_source"] = score.mf.sha256_file(path)
    (world.repo / "config/rewriter-inputs.toml").write_text(
        "".join(f'{key} = "{value}"\n' for key, value in world.pins.items()), encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-CHECK: 100 balanced synthetic sources and a tiny lexicon."""
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "var/ste").mkdir(parents=True)
    rows = []
    for n in range(100):
        raw = "Use `item`." if n < 37 else "Use the model. " * (16 if n < 67 else 35)
        rows.append({"id": f"t{n:03d}", "kind": "final" if n >= 90 else "narration",
                     "uuid": f"u{n}", "loc": {"msg": n, "field": "content"}, "raw": raw})
    for size in (100, 500):
        extra = [{**rows[0], "id": f"extra{n}", "uuid": f"extra{n}"} for n in range(size - 100)]
        save_rows(repo / f"var/ste/kit-{size}/source.jsonl", rows + extra)
    dictionary = repo / "var/ste/approved-verbs.json"
    dictionary.write_text("{}\n", encoding="utf-8")
    folded = b"Rules and four examples.\n"
    pins = {"approved_verbs": score.mf.sha256_file(dictionary),
            "folded_prompt": score.build.digest(folded)}
    prompt = {"system": "Fixture rules.", "examples": [
        {"role": "assistant", "content": "Use the model with care."}], "meta": {
            "limit_instruction": 20, "limit_description": 25,
            "verbs_sha256": pins["approved_verbs"]}}
    pins["system_text"] = score.build.digest(prompt["system"].encode())
    pins["single_text_bundle"] = score.build.digest(
        json.dumps([prompt["system"], prompt["examples"]]).encode())
    prompt["meta"]["prompt_sha256"] = pins["single_text_bundle"]
    (repo / "var/ste" / score.build.PROMPT).write_text(json.dumps(prompt), encoding="utf-8")
    world = SimpleNamespace(repo=repo, pins=pins, rows=rows, folded=folded)
    save_pins(world)
    monkeypatch.setattr(score.ste_check, "REPO", repo)
    monkeypatch.setattr(score.ste_score, "REPO", repo)
    lex = score.ste_check.Lexicon.build(
        {"use": ["v"], "the": ["art"], "model": ["n"], "and": ["conj"]},
        {}, {}, [], [], ["use"])
    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(lambda cls: lex))
    return world


def outputs_for(kit):
    """REWRITE-CHECK, REWRITE-RECORD: synthetic outputs and raw decode metadata."""
    return [{"id": row["id"], "output": row["text"], "finish_reason": "stop",
             "token_ids": [4, 5, 6], "wall_seconds": 0.25} for row in kit["rows"]]


def test_rewrite_kit_preserves_all_ids_masks_maps_and_input_hash(world):
    """REWRITE-KIT: both model paths share the same 100 inputs and original maps."""
    kit = score.load_kit(world.repo)
    assert len(kit["rows"]) == 100 and kit["source_seed"] == 2030
    assert [row["id"] for row in kit["rows"]] == [row["id"] for row in world.rows]
    first = kit["rows"][0]
    assert first["text"] == "Use [[C1]]." and first["codes"] == ["`item`"]
    assert score.ste_convert.unmask(first["text"], first["codes"]) == first["raw"]
    assert [sum(row["bin"] == n for row in kit["rows"]) for n in range(3)] == [37, 30, 33]
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    assert kit["input_sha256"] == score.build.digest(score.jsonl_bytes(inputs))


@pytest.mark.parametrize("size", [100, 500])
def test_rewrite_kit_bad_hash_stops_before_masking(world, monkeypatch, size):
    """REWRITE-KIT: both pinned kits must pass before any derived input is made."""
    path = world.repo / f"var/ste/kit-{size}/source.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")

    def forbidden(text):
        """REWRITE-KIT: detect use of a source before both pins are checked."""
        pytest.fail("mask read an unverified kit")

    monkeypatch.setattr(score.ste_convert, "mask", forbidden)
    with pytest.raises(ValueError, match="sha256 differs"):
        score.load_kit(world.repo)


@pytest.mark.parametrize("change, message", [
    (lambda rows: rows.pop(), "100 source records"),
    (lambda rows: rows[1].update(id=rows[0]["id"]), "duplicate"),
    (lambda rows: rows[1].update(uuid=rows[0]["uuid"]), "duplicate trajectory"),
    (lambda rows: rows[0].update(kind="final"), "90/10"),
    (lambda rows: rows[0].update(raw="Use the model. " * 16), "37/30/33"),
])
def test_rewrite_kit_rejects_changed_population(world, change, message):
    """REWRITE-KIT: fixed membership, kinds and bins cannot silently shrink or move."""
    change(world.rows)
    save_rows(world.repo / "var/ste/kit-100/source.jsonl", world.rows)
    save_pins(world)
    with pytest.raises(ValueError, match=message):
        score.load_kit(world.repo)


@pytest.mark.parametrize("text, budget", [("", 256), ("a", 256), ("ab", 257),
                                         ("\u00b0" * 3, 257), ("a" * 15872, 8192),
                                         ("a" * 20000, 8192)])
def test_rewrite_decode_reply_budget(text, budget):
    """REWRITE-DECODE: count characters and cap the exact reference budget at 8192."""
    assert score.reply_budget(text) == budget


@pytest.mark.parametrize("overflow", [0, 1])
def test_rewrite_decode_checks_last_prompt_and_exact_boundary(world, overflow):
    """REWRITE-DECODE: the whole plan must fit, including the last reply reserve."""
    kit = score.load_kit(world.repo)
    calls = []

    def apply(messages, **kwargs):
        """RENDER-LAYOUT, REWRITE-DECODE: inspect exact shared template options."""
        calls.append((messages, kwargs))
        assert kwargs == {"tokenize": True, "return_dict": False,
                          "add_generation_prompt": True, "enable_thinking": False}
        assert messages[0] == {"role": "system", "content": world.folded.decode()}
        assert len(messages) == 2
        if len(calls) == 100:
            return [1] * (49152 - score.reply_budget(kit["rows"][-1]["text"]) + overflow)
        return [1, 2, 3]

    tok = SimpleNamespace(apply_chat_template=apply)
    if overflow:
        with pytest.raises(score.ScoreError, match="t099.*exceeds 49152"):
            score.preflight(tok, kit, world.folded, repo=world.repo)
    else:
        plan = score.preflight(tok, kit, world.folded, repo=world.repo)
        assert len(plan) == 100
        assert len(plan[-1]["input_ids"]) + plan[-1]["max_new_tokens"] == 49152
        assert calls[0][0][1] == {"role": "user", "content": "<<<TEXT\nUse [[C1]].\nTEXT>>>"}
        framed = json.dumps(calls[0][0], ensure_ascii=True, separators=(",", ":")).encode()
        assert plan[0]["initial_messages_sha256"] == score.build.digest(framed)
    assert len(calls) == 100


def test_rewrite_decode_refuses_wrong_fold_before_template(world):
    """RENDER-SEED: a different data pin stops preflight without a seed claim."""
    with pytest.raises(score.ScoreError, match="folded prompt sha256"):
        score.preflight(None, score.load_kit(world.repo), world.folded + b"\n", repo=world.repo)


def test_rewrite_check_missing_truncated_and_empty_stay_failures(world, tmp_path, monkeypatch):
    """REWRITE-CHECK: retain raw text and 100 slots with explicit CLI inputs."""
    kit = score.load_kit(world.repo)
    outputs = outputs_for(kit)
    outputs[0]["output"] = " \nUse [[C1]].\n "
    outputs[1]["finish_reason"] = "length"
    outputs[2]["output"] = " \n "
    outputs[3]["output"] = None
    outputs.pop(4)
    original = copy.deepcopy(outputs)
    calls = []
    main = score.ste_score.main

    def observed(argv):
        """REWRITE-CHECK: exercise the frozen scorer with both explicit arguments."""
        calls.append(argv)
        return main(argv)

    monkeypatch.setattr(score.ste_score, "main", observed)
    out = tmp_path / "checks"
    summary, checks = score.check_outputs(kit, outputs, out, repo=world.repo)
    assert summary["passed"] == 96 and summary["denominator"] == 100
    assert summary["rate"] == 0.96
    assert summary["failure_rules"] == {"missing": 4}
    assert summary["empty_outputs"] == 1
    scorer_rows = [json.loads(line) for line in (out / "output.jsonl").read_bytes().splitlines()]
    assert all(row["id"] != kit["rows"][2]["id"] for row in scorer_rows)
    assert summary["by_kind"] == {"narration": {"total": 90, "passed": 86},
                                   "final": {"total": 10, "passed": 10}}
    assert summary["by_bin"]["0"] == {"total": 37, "passed": 33}
    assert checks[0]["output"] == "Use [[C1]]." and checks[0]["raw"] == original[0]
    assert checks[1]["raw"]["finish_reason"] == "length"
    assert all(checks[i]["check"]["hard"] == {"missing": [True]} for i in (1, 2, 3, 4))
    assert outputs == original
    assert calls == [[str(out / "output.jsonl"), "--input", str(out / "input.jsonl"),
                      "--prompt", str(out / score.build.PROMPT), "--md", str(out / "score.md")]]
    assert "96/100 pass" in (out / "score.md").read_text(encoding="utf-8")
    assert (out / "score.md").read_text(encoding="utf-8").count("\n## ") == 100
    assert score.mf.verify_tree(score.mf.load_tree(out.with_name("checks.manifest.json")), out) == []


@pytest.mark.parametrize("text, finish, empty_count", [
    ("", "stop", 1),
    (" \t\r\n ", "stop", 1),
    ("Use the model.", "length", 0),
    ("", "length", 1),
    (None, "stop", 0),
])
def test_rewrite_check_withholds_absent_and_truncated_text(
        world, tmp_path, text, finish, empty_count):
    """REWRITE-CHECK: a short source cannot pass with an empty first output."""
    world.rows[0]["raw"] = "Use the model."
    save_rows(world.repo / "var/ste/kit-100/source.jsonl", world.rows)
    save_pins(world)
    kit = score.load_kit(world.repo)
    outputs = outputs_for(kit)
    outputs[0].update(output=text, finish_reason=finish)
    original = copy.deepcopy(outputs)
    out = tmp_path / "withheld"
    summary, checks = score.check_outputs(kit, outputs, out, repo=world.repo)
    assert summary["denominator"] == 100 and summary["passed"] == 99
    assert summary["scorer_outputs"] == 99
    assert summary["empty_outputs"] == empty_count
    assert summary["failure_rules"] == {"missing": 1}
    assert checks[0]["check"] == {
        "ok": False, "hard": {"missing": [True]}, "soft": {}}
    assert checks[0]["scorer_included"] is False
    assert checks[0]["raw"] == original[0] and outputs == original
    written = [json.loads(line) for line in (out / "output.jsonl").read_bytes().splitlines()]
    assert all(row["id"] != outputs[0]["id"] for row in written)
    retained = json.loads((out / "raw.jsonl").read_bytes().splitlines()[0])
    assert retained["raw"] == original[0] and retained["scorer_included"] is False
    assert "99/100 pass" in (out / "score.md").read_text()


@pytest.mark.parametrize("change, message", [
    (lambda rows: rows.append(rows[0].copy()), "duplicate or unknown"),
    (lambda rows: rows[0].update(id="unknown"), "duplicate or unknown"),
    (lambda rows: rows[0].update(finish_reason="other"), "unknown finish"),
    (lambda rows: rows[0].update(output=123), "not text"),
    (lambda rows: rows[0].update(token_ids=[True]), "invalid raw tokens"),
    (lambda rows: rows[0].update(wall_seconds=float("nan")), "invalid wall time"),
    (lambda rows: rows[0].update(wall_seconds=float("inf")), "invalid wall time"),
])
def test_rewrite_check_rejects_bad_outputs_before_lexicon(world, tmp_path, monkeypatch, change, message):
    """REWRITE-CHECK: no duplicate, unknown or malformed output can reach the scorer."""
    kit = score.load_kit(world.repo)
    outputs = outputs_for(kit)
    change(outputs)

    def forbidden(cls):
        """REWRITE-CHECK: input validation must precede lexicon loading."""
        pytest.fail("lexicon loaded before output validation")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    with pytest.raises(score.ScoreError, match=message):
        score.check_outputs(kit, outputs, tmp_path / "bad", repo=world.repo)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("absent", [False, True])
def test_rewrite_check_dictionary_failure_precedes_lexicon(world, tmp_path, monkeypatch, absent):
    """REWRITE-CHECK: an absent or changed approved dictionary stops before use."""
    kit = score.load_kit(world.repo)
    path = world.repo / "var/ste/approved-verbs.json"
    if absent:
        path.unlink()
    else:
        path.write_text("changed", encoding="utf-8")

    def forbidden(cls):
        """REWRITE-CHECK: detect a dictionary read past its failed pin check."""
        pytest.fail("lexicon loaded after dictionary failure")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    with pytest.raises((ValueError, FileNotFoundError)):
        score.check_outputs(kit, [], tmp_path / "bad", repo=world.repo)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("module", ["ste_check", "ste_score"])
def test_rewrite_check_rejects_other_scorer_root_before_lexicon(
        world, tmp_path, monkeypatch, module):
    """REWRITE-CHECK: both scorer passes must read the verified repository."""
    kit = score.load_kit(world.repo)
    monkeypatch.setattr(getattr(score, module), "REPO", tmp_path / "other-root")

    def forbidden(cls):
        """REWRITE-CHECK: root validation must precede any lexicon read."""
        pytest.fail("lexicon loaded from an unverified root")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    out = tmp_path / "root-mismatch"
    with pytest.raises(score.ScoreError, match="repository differs"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    assert not out.exists()


@pytest.mark.parametrize("change", [
    lambda prompt: prompt.update(system="Different rules."),
    lambda prompt: prompt["meta"].update(prompt_sha256="wrong"),
    lambda prompt: prompt["meta"].update(limit_instruction=25),
    lambda prompt: prompt["meta"].update(verbs_sha256="wrong"),
])
def test_rewrite_check_prompt_failure_precedes_lexicon(world, tmp_path, monkeypatch, change):
    """REWRITE-CHECK: changed source text, limits or identities cannot reach scoring."""
    kit = score.load_kit(world.repo)
    path = world.repo / "var/ste" / score.build.PROMPT
    prompt = json.loads(path.read_bytes())
    change(prompt)
    path.write_text(json.dumps(prompt), encoding="utf-8")

    def forbidden(cls):
        """REWRITE-CHECK: the prompt must pass before lexicon loading."""
        pytest.fail("lexicon loaded after prompt failure")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    with pytest.raises(score.ScoreError, match="REWRITE-CHECK"):
        score.check_outputs(kit, [], tmp_path / "bad", repo=world.repo)


def test_rewrite_check_system_pin_fails_independently(world, tmp_path, monkeypatch):
    """REWRITE-CHECK: the system pin must pass even when the bundle pin passes."""
    kit = score.load_kit(world.repo)
    world.pins["system_text"] = "0" * 64
    save_pins(world)

    def forbidden(cls):
        """REWRITE-CHECK: validate all prompt identities before lexicon loading."""
        pytest.fail("lexicon loaded after a system pin failure")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    with pytest.raises(score.ScoreError, match="source system sha256 differs"):
        score.check_outputs(kit, [], tmp_path / "bad-system", repo=world.repo)


def test_rewrite_record_refuses_output_inside_git(world):
    """REWRITE-RECORD: artifacts cannot be written to a nonignored repository path."""
    subprocess.run(["git", "init", "--quiet", str(world.repo)], check=True)
    kit = score.load_kit(world.repo)
    out = world.repo / "results"
    with pytest.raises(score.ScoreError, match="outputs must stay outside git"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    assert not out.exists()


def test_rewrite_record_accepts_ignored_output_inside_repo(world):
    """REWRITE-RECORD: an ignored artifact and sibling manifest stay outside git."""
    subprocess.run(["git", "init", "--quiet", str(world.repo)], check=True)
    (world.repo / ".gitignore").write_text(
        "results/\nresults.manifest.json\n", encoding="utf-8")
    kit = score.load_kit(world.repo)
    out = world.repo / "results"
    summary, _ = score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    assert summary["passed"] == 100
    manifest = score.mf.load_tree(world.repo / "results.manifest.json")
    assert score.mf.verify_tree(manifest, out) == []


def test_rewrite_kit_refuses_text_changed_after_load(world, tmp_path, monkeypatch):
    """REWRITE-KIT: changed in-memory inputs cannot reach either checker pass."""
    kit = score.load_kit(world.repo)
    kit["rows"][0]["text"] = "Use [[C1]] with care."

    def forbidden(cls):
        """REWRITE-KIT: the frozen input hash must pass before lexicon loading."""
        pytest.fail("lexicon loaded for a changed input")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    out = tmp_path / "changed-input"
    with pytest.raises(score.ScoreError, match="masked inputs changed"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    failure = json.loads((out / "failure.json").read_bytes())
    assert failure["completed_ids"] == []
    assert not (out / "checks-summary.json").exists()


def test_rewrite_kit_refuses_changed_bins_with_unchanged_counts(world, tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-SPREAD: bind each result to its source length bin."""
    kit = score.load_kit(world.repo)
    first, second = kit["rows"][0], kit["rows"][40]
    first["bin"], second["bin"] = second["bin"], first["bin"]
    assert [sum(row["bin"] == n for row in kit["rows"]) for n in range(3)] == [37, 30, 33]

    def forbidden(cls):
        """REWRITE-KIT: reject swapped bins before any checker pass."""
        pytest.fail("lexicon loaded for changed source bins")

    monkeypatch.setattr(score.ste_check.Lexicon, "load", classmethod(forbidden))
    out = tmp_path / "changed-bins"
    with pytest.raises(score.ScoreError, match="source length bins changed"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    failure = json.loads((out / "failure.json").read_bytes())
    assert failure["completed_ids"] == []
    assert not (out / "checks-summary.json").exists()
    assert not out.with_name("changed-bins.manifest.json").exists()


def test_rewrite_check_enforces_instruction_limit_in_both_passes(world, tmp_path):
    """REWRITE-CHECK: a 22-word instruction must fail both checker passes."""
    world.rows[0]["raw"] = "Use " + "the model " * 10 + "the."
    save_rows(world.repo / "var/ste/kit-100/source.jsonl", world.rows)
    save_pins(world)
    kit = score.load_kit(world.repo)
    out = tmp_path / "long-instruction"
    summary, checks = score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    assert summary["passed"] == 99
    length = checks[0]["check"]["hard"]["length"][0]
    assert (length["words"], length["limit"]) == (22, 20)
    report = (out / "score.md").read_text(encoding="utf-8")
    assert "99/100 pass." in report and "## t000 narration: FAIL length" in report


@pytest.mark.parametrize("headline", ["99/100 pass.", "100/99 pass.", "Missing headline."])
def test_rewrite_check_refuses_reference_headline_divergence(
        world, tmp_path, monkeypatch, headline):
    """REWRITE-CHECK: a different frozen-scorer total cannot publish a summary."""
    kit = score.load_kit(world.repo)
    main = score.ste_score.main

    def divergent(argv):
        """REWRITE-CHECK: simulate a different reference count after scoring."""
        status = main(argv)
        report = Path(argv[argv.index("--md") + 1])
        lines = report.read_text(encoding="utf-8").splitlines()
        lines[2] = headline
        report.write_text("\n".join(lines), encoding="utf-8")
        return status

    monkeypatch.setattr(score.ste_score, "main", divergent)
    out = tmp_path / "divergence"
    with pytest.raises(score.ScoreError, match="REWRITE-CHECK"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    failure = json.loads((out / "failure.json").read_bytes())
    assert failure["completed_ids"] == [row["id"] for row in kit["rows"]]
    assert not (out / "checks-summary.json").exists()
    assert not out.with_name("divergence.manifest.json").exists()


def test_rewrite_check_all_missing_keeps_denominator_and_manifest(world, tmp_path):
    """REWRITE-CHECK: an empty output file remains zero accepted results out of 100."""
    kit = score.load_kit(world.repo)
    out = tmp_path / "all-missing"
    summary, checks = score.check_outputs(kit, [], out, repo=world.repo)
    assert (summary["passed"], summary["denominator"], summary["scorer_outputs"]) == (0, 100, 0)
    assert summary["failure_rules"] == {"missing": 100}
    assert len(checks) == 100 and all(row["raw"] is None for row in checks)
    assert (out / "output.jsonl").read_bytes() == b""
    assert "0/100 pass" in (out / "score.md").read_text(encoding="utf-8")
    assert score.mf.verify_tree(score.mf.load_tree(out.with_name("all-missing.manifest.json")), out) == []


def test_rewrite_kit_detects_source_change_during_masking(world, monkeypatch):
    """REWRITE-KIT: final input verification detects a source change after its read."""
    mask = score.ste_convert.mask
    calls = []
    path = world.repo / "var/ste/kit-100/source.jsonl"

    def change(text):
        """REWRITE-KIT: replace a source after all source bytes have been captured."""
        calls.append(text)
        if len(calls) == 100:
            path.write_bytes(path.read_bytes() + b"\n")
        return mask(text)

    monkeypatch.setattr(score.ste_convert, "mask", change)
    with pytest.raises(ValueError, match="file changed"):
        score.load_kit(world.repo)


def test_rewrite_check_matches_reference_and_records_literal_failures(world, tmp_path):
    """REWRITE-CHECK: retain frozen acceptance and separate literal loss records."""
    world.rows[0]["raw"] = "Use `item`:`value`."
    save_rows(world.repo / "var/ste/kit-100/source.jsonl", world.rows)
    save_pins(world)
    kit = score.load_kit(world.repo)
    outputs = outputs_for(kit)
    outputs[0]["output"] = "Use [[C1]] and [[C2]]."
    outputs[1]["output"] = "Use [[C1]]. <tag>"
    outputs[2]["output"] = "Use the model."
    outputs[40]["output"] = "Use the model with care."
    out = tmp_path / "checked"
    summary, checks = score.check_outputs(kit, outputs, out, repo=world.repo)
    assert checks[0]["check"]["ok"] is True
    assert checks[0]["literal_failures"] == ["[[C1]]:[[C2]]"]
    assert "invented_markup" in checks[1]["check"]["hard"]
    assert "placeholders" in checks[2]["check"]["hard"]
    assert "copied_example" in checks[40]["check"]["hard"]
    reference = tmp_path / "reference.md"
    assert score.ste_score.main([
        str(out / "output.jsonl"), "--input", str(out / "input.jsonl"),
        "--prompt", str(out / score.build.PROMPT), "--md", str(reference)]) == 0
    assert reference.read_bytes() == (out / "score.md").read_bytes()
    headings = [line for line in reference.read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")]
    assert len(headings) == 100
    assert [line.endswith(": pass") for line in headings] == [r["check"]["ok"] for r in checks]
    assert summary["passed"] == sum(line.endswith(": pass") for line in headings)


def test_rewrite_record_retains_checker_failure_and_refuses_reuse(world, tmp_path, monkeypatch):
    """REWRITE-RECORD: retain failed checks and refuse directory reuse."""
    kit = score.load_kit(world.repo)
    out = tmp_path / "failed"

    def fail(argv):
        """REWRITE-RECORD: fail after raw checker inputs are written."""
        raise RuntimeError("fixture checker failure")

    monkeypatch.setattr(score.ste_score, "main", fail)
    with pytest.raises(RuntimeError, match="fixture checker failure"):
        score.check_outputs(kit, outputs_for(kit), out, repo=world.repo)
    assert len((out / "checks.jsonl").read_bytes().splitlines()) == 100
    assert json.loads((out / "failure.json").read_bytes())["status"] == "failed"
    assert not (out / "checks-summary.json").exists()
    assert not out.with_name("failed.manifest.json").exists()
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(score.ScoreError, match="already exists"):
        score.check_outputs(kit, [], out, repo=world.repo)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_rewrite_record_keeps_raw_outputs_and_completed_ids_on_row_failure(
        world, tmp_path, monkeypatch):
    """REWRITE-RECORD: keep raw outputs and completed checks after a row raises."""
    kit = score.load_kit(world.repo)
    outputs = outputs_for(kit)
    check = score.ste_check.check
    calls = []

    def fail_second(*args):
        """REWRITE-RECORD: fail after one completed per-ID check."""
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("fixture per-ID failure")
        return check(*args)

    monkeypatch.setattr(score.ste_check, "check", fail_second)
    out = tmp_path / "row-failure"
    with pytest.raises(RuntimeError, match="fixture per-ID failure"):
        score.check_outputs(kit, outputs, out, repo=world.repo)
    raw = [json.loads(line) for line in (out / "raw.jsonl").read_bytes().splitlines()]
    checks = [json.loads(line) for line in (out / "checks.jsonl").read_bytes().splitlines()]
    assert [row["raw"] for row in raw] == outputs
    assert [row["id"] for row in checks] == [kit["rows"][0]["id"]]
    failure = json.loads((out / "failure.json").read_bytes())
    assert failure["completed_ids"] == [kit["rows"][0]["id"]]
    assert not (out / "checks-summary.json").exists()
    assert not out.with_name("row-failure.manifest.json").exists()


def test_rewrite_check_rejects_dictionary_change_during_reference_read(world, tmp_path, monkeypatch):
    """REWRITE-CHECK: a late input change cannot produce a successful checker record."""
    kit = score.load_kit(world.repo)
    main = score.ste_score.main

    def change(argv):
        """REWRITE-CHECK: change a captured input after the second lexicon read."""
        result = main(argv)
        (world.repo / "var/ste/approved-verbs.json").write_text("changed", encoding="utf-8")
        return result

    monkeypatch.setattr(score.ste_score, "main", change)
    out = tmp_path / "late-change"
    with pytest.raises(ValueError, match="file changed"):
        score.check_outputs(kit, [], out, repo=world.repo)
    assert (out / "failure.json").exists()
    assert not (out / "checks-summary.json").exists()
