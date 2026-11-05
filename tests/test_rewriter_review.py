"""Spec 0.2 REWRITE-REVIEW: blind inputs, safe restoration and retained verdicts."""

import copy
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_review as review
from tests.test_rewriter_report import candidate, evidence
from tests.test_rewriter_run_command import command, tokenizer, trained, world

score = review.score
mf = review.mf


@pytest.fixture
def batch(evidence):
    """REWRITE-REVIEW: use verified synthetic runs with missing first outputs."""
    c = evidence.command
    out = evidence.first.parent / "batch"
    sources = [(evidence.first, None, None)]
    record, manifest = review.prepare(c.plan, sources, out, repo=c.repo)
    audit = json.loads((out / "private.json").read_bytes())
    verdict_file = out.parent / "external-verdicts.jsonl"
    verdicts = [{"review_id": row["review_id"], "verdict": "faithful", "defects": []}
                for row in audit["sequence"]]
    verdict_file.write_bytes(score.jsonl_bytes(verdicts))
    return SimpleNamespace(evidence=evidence, repo=c.repo, plan=c.plan, out=out,
        sources=sources, record=record, manifest=manifest, audit=audit,
        verdict_file=verdict_file, verdicts=verdicts, result=out.parent / "verdict-record")


def ingest(batch, **kwargs):
    """REWRITE-REVIEW: ingest external fixture verdicts with explicit input pins."""
    options = {"input_sha256": batch.audit["input_sha256"],
               "prompt_sha256": batch.audit["prompt_sha256"], "repo": batch.repo}
    options.update(kwargs)
    return review.ingest(batch.out, batch.plan, batch.sources, batch.verdict_file,
                         batch.result, **options)


def reseal(batch):
    """REWRITE-REVIEW: reach semantic guards with a valid synthetic manifest."""
    mf.build_tree(batch.out, "evaluation", "ste-rewriter-review-batch").save(
        batch.out.with_name(batch.out.name + ".manifest.json"))


def assert_failed(batch, message):
    """REWRITE-RECORD: rejection retains its exact reason and no success manifest."""
    record = json.loads((batch.result / "review.json").read_bytes())
    assert record["status"] == "failed"
    assert record["failure"]["message"] == message
    assert not batch.result.with_name(batch.result.name + ".manifest.json").exists()


def test_review_blind_sequence_restoration_and_new_identity(batch):
    """REWRITE-REVIEW: independently reproduce ordering, pins and source membership."""
    audit = batch.audit
    blind = review.report.rows((batch.out / "reviewer/input.jsonl").read_bytes())
    assert all(set(row) == {"review_id", "source", "rewrite"} for row in blind)
    assert sorted(p.name for p in (batch.out / "reviewer").iterdir()) == ["input.jsonl", "prompt.md"]
    assert audit["sample"] == random.Random(2031).sample([f"t{n:03d}" for n in range(100)], 30)
    r = json.loads((batch.evidence.first / "run.json").read_bytes())
    expected = [(r["run_id"], key) for key in sorted(audit["sample"]) if int(key[1:]) >= 10]
    random.Random(2032).shuffle(expected)
    assert [(row["run_id"], row["source_id"]) for row in audit["sequence"]] == expected
    assert all(row["source_id"] in audit["sample"] for row in audit["unreviewed"])
    assert len(audit["sequence"]) + len(audit["unreviewed"]) == 30
    assert all(row["source"].strip() == row["rewrite"] for row in blind)
    assert any("`item`" in row["rewrite"] for row in blind)
    assert all("[[C1]]" not in row["rewrite"] for row in blind)
    assert audit["input_sha256"] == mf.sha256_file(batch.out / "reviewer/input.jsonl")
    assert audit["prompt_sha256"] == mf.sha256_file(batch.repo / "config/ste-rewrite-review-prompt.md")
    assert mf.verify_tree(batch.manifest, batch.out) == []
    before = (batch.out / "batch.json").read_bytes()
    again = batch.out.with_name("another-batch")
    record, _ = review.prepare(batch.plan, list(reversed(batch.sources)), again, repo=batch.repo)
    other = json.loads((again / "private.json").read_bytes())
    assert record["batch_id"] != batch.record["batch_id"]
    assert [(row["run_id"], row["source_id"]) for row in other["sequence"]] == expected
    assert {r["review_id"] for r in other["sequence"]}.isdisjoint(r["review_id"] for r in blind)
    with pytest.raises(ValueError, match="output directory or manifest already exists"):
        review.prepare(batch.plan, batch.sources, batch.out, repo=batch.repo)
    assert (batch.out / "batch.json").read_bytes() == before


def test_review_missing_unsafe_and_safe_truncated_first_rewrites(batch):
    """REWRITE-REVIEW: withhold unsafe code restoration without replacing sample IDs."""
    bundle, _, runs = review.read_sources(batch.plan, batch.sources, batch.repo)
    source_id = next(row["source_id"] for row in batch.audit["sequence"]
                     if row["codes"])
    row = next(row for row in runs[0]["checks"] if row["id"] == source_id)
    row["output"] = "Use [[C999]]."
    _, mapping, missing = review.sequence(bundle, runs, batch.record["batch_id"])
    assert not any(r["source_id"] == source_id and r["run_id"] == runs[0]["record"]["run_id"]
                   for r in mapping)
    assert any(r["source_id"] == source_id and r["reasons"] == ["placeholder_failure"]
               for r in missing)
    row["output"] = "Use [[C1]]."
    row["literal_failures"] = ["fixture literal"]
    _, _, missing = review.sequence(bundle, runs, batch.record["batch_id"])
    assert any(r["source_id"] == source_id and r["reasons"] == ["literal_failure"] for r in missing)
    row["literal_failures"] = []
    row["raw"]["finish_reason"] = "length"
    row["check"]["ok"] = False
    _, mapping, _ = review.sequence(bundle, runs, batch.record["batch_id"])
    kept = next(r for r in mapping if r["source_id"] == source_id and
                r["run_id"] == runs[0]["record"]["run_id"])
    assert kept["finish_reason"] == "length" and not kept["checker_accepted"]
    assert kept["rewrite"] == "Use `item`."


def test_review_restoration_mismatch_rejected(batch):
    """REWRITE-REVIEW: never accept a source map that restores different text."""
    bundle, _, runs = review.read_sources(batch.plan, batch.sources, batch.repo)
    source_id = batch.audit["sequence"][0]["source_id"]
    next(row for row in bundle[1]["rows"] if row["id"] == source_id)["raw"] = "changed"
    with pytest.raises(ValueError, match="^REWRITE-REVIEW: source code restoration differs$"):
        review.sequence(bundle, runs, batch.record["batch_id"])


def test_review_input_sources_and_prompt_guards(batch, monkeypatch):
    """REWRITE-REVIEW: each prompt and membership guard has its own rejection."""
    with pytest.raises(ValueError, match="^REWRITE-REVIEW: at least one score is required$"):
        review.read_sources(batch.plan, [], batch.repo)
    with pytest.raises(ValueError, match="^REWRITE-REVIEW: score identity repeats$"):
        review.read_sources(batch.plan, batch.sources * 2, batch.repo)
    bundle = score.read_plan(batch.plan, repo=batch.repo)
    monkeypatch.setattr(score, "read_plan", lambda *a, **kw: bundle)
    (batch.repo / "config/ste-rewrite-review-prompt.md").write_bytes(b"changed")
    with pytest.raises(ValueError, match="^REWRITE-REVIEW: review prompt bytes differ$"):
        review.read_sources(batch.plan, batch.sources, batch.repo)


@pytest.mark.parametrize("field, message", [
    ("schema", "REWRITE-REVIEW: a completed review batch is required"),
    ("input", "REWRITE-REVIEW: retained blind sequence differs"),
    ("prompt", "REWRITE-REVIEW: retained review prompt differs"),
    ("map", "REWRITE-REVIEW: retained private map differs"),
    ("summary", "REWRITE-REVIEW: batch summary differs"),
    ("batch_id", "REWRITE-REVIEW: batch identity must be a UUID"),
    ("manifest_kind", "REWRITE-REVIEW: expected a review batch manifest"),
    ("reviewer_leak", "REWRITE-REVIEW: review batch file set differs"),
])
def test_review_resealed_batch_does_not_change_reviewed_sequence(batch, field, message):
    """REWRITE-REVIEW: file hashes cannot authorize changed blind inputs or maps."""
    path = batch.out / "batch.json"
    record = json.loads(path.read_bytes())
    if field == "schema":
        record["schema"] = "another"
    elif field == "summary":
        record["available"] += 1
    elif field == "batch_id":
        record["batch_id"] = str(__import__("uuid").UUID(record["batch_id"]))
    elif field == "input":
        (batch.out / "reviewer/input.jsonl").write_bytes(b"{}\n")
    elif field == "prompt":
        (batch.out / "reviewer/prompt.md").write_bytes(b"different")
    elif field == "reviewer_leak":
        (batch.out / "reviewer/map.json").write_bytes(b'{"procedure": "r0"}')
    elif field == "map":
        changed = copy.deepcopy(batch.audit)
        changed["sequence"][0]["run_id"] = "different"
        (batch.out / "private.json").write_bytes(review.json_bytes(changed))
    path.write_bytes(review.json_bytes(record))
    reseal(batch)
    if field == "manifest_kind":
        mf.build_tree(batch.out, "dataset", "different").save(batch.out.with_name("batch.manifest.json"))
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch)
    assert_failed(batch, message)


def test_review_records_original_overrides_and_distinct_denominators(batch):
    """REWRITE-REVIEW: the effective verdict never replaces the original evidence."""
    original = batch.verdict_file.read_bytes()
    row = batch.audit["sequence"][0]
    override = {"review_id": row["review_id"], "verdict": "wrong", "defects": [
        {"source_quote": "Use", "rewrite_quote": "", "reason": "First distinct fixture defect"},
        {"source_quote": "Use", "rewrite_quote": "", "reason": "Second distinct fixture defect"}],
        "operator": "Fixture operator", "date": "2026-11-04"}
    overrides = batch.out.parent / "overrides.jsonl"
    overrides.write_bytes(score.jsonl_bytes([override]))
    record, manifest = ingest(batch, overrides=overrides)
    count = len(batch.verdicts)
    assert record["summary"] == {"verdicts": {"faithful": count - 1, "minor drift": 0,
        "wrong": 1, "data altered": 0}, "reviewed": count,
        "reviewed_checker_accepted": count, "distinct_defects": 2,
        "defects_per_100": 200 / count, "segments_with_defects": 1,
        "segments_with_defects_rate": 1 / count}
    assert record["unreviewed"] == batch.audit["unreviewed"]
    assert record["pending_review_ids"] == []
    rows = review.report.rows((batch.result / "verdicts.jsonl").read_bytes())
    assert rows[0]["original"] == batch.verdicts[0]
    assert rows[0]["override"] == override and rows[0]["effective"]["verdict"] == "wrong"
    assert (batch.result / "original-verdicts.jsonl").read_bytes() == original
    assert record["verdicts_sha256"] == score.build.digest(original)
    assert mf.verify_tree(manifest, batch.result) == []
    before = (batch.result / "review.json").read_bytes()
    with pytest.raises(ValueError, match="output directory or manifest already exists"):
        ingest(batch)
    assert (batch.result / "review.json").read_bytes() == before


def test_review_undefined_rates_and_rejected_checker_results():
    """REWRITE-REVIEW: count defects only among reviewed checker-accepted items."""
    row = {"checker_accepted": False, "effective": {"verdict": "wrong", "defects": [{}]}}
    summary = review.summarize([row])
    assert summary["reviewed"] == 1 and summary["verdicts"]["wrong"] == 1
    assert summary["reviewed_checker_accepted"] == summary["distinct_defects"] == 0
    assert summary["defects_per_100"] is None and summary["segments_with_defects_rate"] is None
    assert review.summarize([])["reviewed"] == 0


@pytest.mark.parametrize("change, message", [
    ("missing", "REWRITE-REVIEW: verdict IDs differ from blind input order"),
    ("duplicate", "REWRITE-REVIEW: verdict IDs differ from blind input order"),
    ("reversed", "REWRITE-REVIEW: verdict IDs differ from blind input order"),
    ("unknown", "REWRITE-REVIEW: verdict IDs differ from blind input order"),
    ("verdict", "REWRITE-REVIEW: invalid verdict fields"),
    ("extra", "REWRITE-REVIEW: invalid verdict fields"),
    ("empty_defects", "REWRITE-REVIEW: verdict and defect count disagree"),
    ("faithful_defects", "REWRITE-REVIEW: verdict and defect count disagree"),
    ("defect_shape", "REWRITE-REVIEW: invalid defect fields"),
    ("quote", "REWRITE-REVIEW: defect quote differs from reviewed text"),
    ("duplicate_defect", "REWRITE-REVIEW: duplicate defect"),
])
def test_review_invalid_external_verdicts_keep_failure_and_raw_bytes(batch, change, message):
    """REWRITE-REVIEW: a malformed or incomplete review cannot become a success."""
    rows = batch.verdicts
    defect = {"source_quote": "Use", "rewrite_quote": "", "reason": "Fixture defect"}
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[1] = rows[0]
    elif change == "reversed":
        rows.reverse()
    elif change == "unknown":
        rows[0]["review_id"] = "unknown"
    elif change == "verdict":
        rows[0]["verdict"] = "good"
    elif change == "extra":
        rows[0]["model"] = "fixture"
    elif change == "empty_defects":
        rows[0]["verdict"] = "wrong"
    elif change == "faithful_defects":
        rows[0]["defects"] = [defect]
    else:
        rows[0].update(verdict="wrong", defects=[defect])
        if change == "defect_shape":
            defect["reason"] = ""
        elif change == "quote":
            defect["source_quote"] = "absent fixture quote"
        else:
            rows[0]["defects"].append(defect.copy())
    raw = score.jsonl_bytes(rows)
    batch.verdict_file.write_bytes(raw)
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch)
    assert_failed(batch, message)
    assert (batch.result / "original-verdicts.jsonl").read_bytes() == raw
    record = json.loads((batch.result / "review.json").read_bytes())
    assert record["pending_review_ids"] == [row["review_id"] for row in batch.audit["sequence"]]


@pytest.mark.parametrize("key", ["input_sha256", "prompt_sha256"])
def test_review_submitted_input_pins_are_checked(batch, key):
    """REWRITE-REVIEW: verdict import is bound to the actual grader input bytes."""
    message = "REWRITE-REVIEW: submitted reviewer input or prompt hash differs"
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch, **{key: "changed"})
    assert_failed(batch, message)


@pytest.mark.parametrize("change, message", [
    ("fields", "REWRITE-REVIEW: invalid override fields"),
    ("unknown", "REWRITE-REVIEW: duplicate or unknown override ID"),
    ("duplicate", "REWRITE-REVIEW: duplicate or unknown override ID"),
    ("operator", "REWRITE-REVIEW: override needs an operator and absolute date"),
    ("date", "REWRITE-REVIEW: override needs an operator and absolute date"),
])
def test_review_override_identity_and_attribution_guards(batch, change, message):
    """REWRITE-REVIEW: no anonymous, undated or invented operator verdicts."""
    row = {**batch.verdicts[0], "operator": "Fixture operator", "date": "2026-11-04"}
    rows = [row]
    if change == "fields":
        row.pop("operator")
    elif change == "unknown":
        row["review_id"] = "unknown"
    elif change == "duplicate":
        rows.append(row.copy())
    elif change == "operator":
        row["operator"] = " "
    else:
        row["date"] = "20261104"
    path = batch.out.parent / "bad-overrides.jsonl"
    path.write_bytes(score.jsonl_bytes(rows))
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch, overrides=path)
    assert_failed(batch, message)


def test_review_source_change_during_preparation_keeps_failure(batch, monkeypatch):
    """REWRITE-RECORD: changes across the preparation window cannot be sealed."""
    original = review.payload
    calls = []

    def changed(*args):
        result = original(*args)
        calls.append(True)
        return result if len(calls) == 1 else (b"changed", result[1], result[2])

    monkeypatch.setattr(review, "payload", changed)
    out = batch.out.with_name("changed-preparation")
    message = "REWRITE-RECORD: review sources changed during preparation"
    with pytest.raises(ValueError, match="^" + message + "$"):
        review.prepare(batch.plan, batch.sources, out, repo=batch.repo)
    assert json.loads((out / "batch.json").read_bytes())["failure"]["message"] == message
    assert not out.with_name(out.name + ".manifest.json").exists()


@pytest.mark.parametrize("target", ["batch", "verdicts"])
def test_review_ingestion_window_changes_keep_failure(batch, monkeypatch, target):
    """REWRITE-RECORD: retain rejection if a reviewed input changes before sealing."""
    original = review.read_batch
    calls = []

    def changed(*args):
        audit, identity = original(*args)
        calls.append(True)
        if len(calls) == 2:
            if target == "batch":
                identity["tree_hash"] = "changed"
            else:
                batch.verdict_file.write_bytes(b"changed")
        return audit, identity

    monkeypatch.setattr(review, "read_batch", changed)
    message = ("REWRITE-RECORD: review batch changed during ingestion" if target == "batch"
               else "REWRITE-RECORD: verdict input changed during ingestion")
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch)
    assert_failed(batch, message)


def test_review_cli_uses_only_local_files(batch, capsys):
    """REWRITE-REVIEW: exercise both public commands without a reviewer call."""
    args = ["--plan", str(batch.plan), "--run", str(batch.evidence.first)]
    assert review.main(["prepare", *args, "--out", str(batch.out)], repo=batch.repo) == 2
    fresh = batch.out.with_name("cli-batch")
    capsys.readouterr()
    assert review.main(["prepare", *args, "--out", str(fresh)], repo=batch.repo) == 0
    output = json.loads(capsys.readouterr().out.splitlines()[-1])
    manifest = mf.load_tree(fresh.with_name(fresh.name + ".manifest.json"))
    assert output == {"status": "completed", "tree_hash": manifest.tree_hash}
    assert mf.verify_tree(manifest, fresh) == []
    assert review.main(["ingest", *args, "--out", str(batch.result),
        "--batch", str(batch.out), "--verdicts", str(batch.verdict_file),
        "--input-sha256", batch.audit["input_sha256"],
        "--prompt-sha256", batch.audit["prompt_sha256"]], repo=batch.repo) == 0


@pytest.mark.parametrize("verdict", review.VERDICTS)
def test_review_accepts_each_defined_verdict(verdict):
    """REWRITE-REVIEW: every specified verdict has a valid external representation."""
    item = {"review_id": "opaque", "source": "Use the item.", "rewrite": "Remove the item."}
    defects = [] if verdict == "faithful" else [{"source_quote": "Use", "rewrite_quote": "Remove",
                                               "reason": "The action changes."}]
    review.validate_verdict({"review_id": "opaque", "verdict": verdict, "defects": defects}, item)


def test_review_sort_uses_procedure_then_epoch_then_source(batch):
    """REWRITE-REVIEW: distinct procedures and epochs precede the seeded shuffle."""
    bundle, _, runs = review.read_sources(batch.plan, batch.sources, batch.repo)
    runs.append(copy.deepcopy(runs[0]))
    runs[1]["record"]["run_id"] = "second"
    runs[0]["record"].update(procedure="r1", epoch=2)
    runs[1]["record"].update(procedure="r1", epoch=1)
    third = copy.deepcopy(runs[0])
    third["record"].update(procedure="r0", epoch=0, run_id="third")
    runs.append(third)
    _, mapping, _ = review.sequence(bundle, runs, batch.record["batch_id"])
    expected = [(run["record"]["procedure"], run["record"]["epoch"], row["id"])
                for run in runs for row in run["checks"]
                if row["id"] in bundle[0]["review_sample"] and row["output"]]
    expected.sort()
    random.Random(2032).shuffle(expected)
    assert [(row["procedure"], row["epoch"], row["source_id"]) for row in mapping] == expected


def test_review_rejects_second_r0_pass(batch):
    """REWRITE-REVIEW, REWRITE-SPREAD: the spread repeat cannot double the sample."""
    out = batch.out.with_name("duplicate-r0")
    sources = [*batch.sources, (batch.evidence.second, None, None)]
    message = "REWRITE-REVIEW: procedure and epoch repeat"
    with pytest.raises(ValueError, match="^" + message + "$"):
        review.prepare(batch.plan, sources, out, repo=batch.repo)
    record = json.loads((out / "batch.json").read_bytes())
    assert record["status"] == "failed" and record["failure"]["message"] == message
    assert not out.with_name(out.name + ".manifest.json").exists()


@pytest.mark.parametrize("keys, duplicate", [
    ([("r0", 0), ("r0", 0)], True),
    ([("r1", 1), ("r1", 1)], True),
    ([("r1", 2), ("r1", 2)], True),
    ([("r2", 1), ("r2", 1)], True),
    ([("r2", 2), ("r2", 2)], True),
    ([("r0", 0), ("r1", 1)], False),
    ([("r1", 1), ("r1", 2)], False),
    ([("r1", 1), ("r2", 1)], False),
])
def test_review_cohort_identity(batch, monkeypatch, keys, duplicate):
    """REWRITE-REVIEW: procedure and epoch jointly identify a compared result."""
    bundle, _, runs = review.read_sources(batch.plan, batch.sources, batch.repo)
    runs.append(review.report.read_score(batch.evidence.second, bundle, repo=batch.repo))
    for run, (procedure, epoch) in zip(runs, keys):
        run["record"].update(procedure=procedure, epoch=epoch)
    supplied = iter(runs)
    monkeypatch.setattr(review.report, "read_score", lambda *a, **kw: next(supplied))
    sources = [*batch.sources, (batch.evidence.second, None, None)]
    if duplicate:
        with pytest.raises(ValueError, match="^REWRITE-REVIEW: procedure and epoch repeat$"):
            review.read_sources(batch.plan, sources, batch.repo)
    else:
        _, _, accepted = review.read_sources(batch.plan, sources, batch.repo)
        assert len(accepted) == 2


def test_review_includes_distinct_verified_adapter_procedure(batch, trained):
    """REWRITE-REVIEW: the first R0 and a verified epoch share one blind sequence."""
    item = candidate(batch.evidence, trained)
    out = batch.out.with_name("r0-and-r1")
    review.prepare(batch.plan, [*batch.sources, item], out, repo=batch.repo)
    audit = json.loads((out / "private.json").read_bytes())
    assert len(audit["sequence"]) + len(audit["unreviewed"]) == 60
    assert {(row["procedure"], row["epoch"]) for row in audit["sequence"]} == {
        ("r0", 0), ("r1", 1)}
    assert len({(row["procedure"], row["epoch"], row["source_id"])
                for row in audit["sequence"]}) == len(audit["sequence"])


@pytest.mark.parametrize("quote", ["", " "])
@pytest.mark.parametrize("target", ["external", "override"])
def test_review_requires_nonempty_source_quote(batch, quote, target):
    """REWRITE-REVIEW: both original and override defects need a source anchor."""
    verdict = {"review_id": batch.verdicts[0]["review_id"], "verdict": "wrong", "defects": [
        {"source_quote": quote, "rewrite_quote": "", "reason": "Fixture omission"}]}
    options = {}
    if target == "external":
        batch.verdicts[0] = verdict
        batch.verdict_file.write_bytes(score.jsonl_bytes(batch.verdicts))
    else:
        verdict.update(operator="Fixture operator", date="2026-11-05")
        overrides = batch.out.parent / "empty-source-override.jsonl"
        overrides.write_bytes(score.jsonl_bytes([verdict]))
        options["overrides"] = overrides
    message = "REWRITE-REVIEW: source quote is empty"
    with pytest.raises(ValueError, match="^" + message + "$"):
        ingest(batch, **options)
    assert_failed(batch, message)


@pytest.mark.parametrize("kind", ["addition", "omission"])
def test_review_source_anchors_allow_added_and_omitted_content(kind):
    """REWRITE-REVIEW: quote source context for additions and an empty rewrite for omissions."""
    item = {"review_id": "opaque", "source": "Use the item.",
            "rewrite": "Use the item. Remove the log." if kind == "addition" else "Use."}
    defect = {"source_quote": "Use the item.",
              "rewrite_quote": "Remove the log." if kind == "addition" else "",
              "reason": "An action was added." if kind == "addition" else "The object was omitted."}
    review.validate_verdict({"review_id": "opaque", "verdict": "wrong", "defects": [defect]}, item)
