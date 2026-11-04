"""Spec 0.2 section 9: verified R0 spread and epoch comparison records."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_report as report
from tests.test_rewriter_run_command import (
    command, make_plan, run, score_adapter, tokenizer, trained, world,
)
from tests.test_rewriter_score import outputs_for

score = report.score
mf = score.mf


@pytest.fixture
def evidence(command, monkeypatch):
    """REWRITE-SPREAD: two completed fixture runs with different first-pass rates."""
    assert make_plan(command) == 0
    failures = [10]

    def decode(loaded, kit, folded, **kwargs):
        """REWRITE-CHECK: missing outputs remain failures in the denominator."""
        for n, row in enumerate(outputs_for(kit)):
            if n < failures[0]:
                row.update(output=None, token_ids=[])
            yield row

    monkeypatch.setattr(score, "decode_outputs", decode)
    first, second = command.out.parent / "first", command.out.parent / "second"
    command.out = first
    assert run(command) == 0
    failures[0] = 11
    command.out = second
    assert run(command) == 0
    failures[0] = 0
    return SimpleNamespace(command=command, first=first, second=second,
                           out=first.parent / "comparison")


def compare(evidence, candidates=()):
    """REWRITE-SPREAD: exercise the production comparison command."""
    args = ["--plan", str(evidence.command.plan), "--r0-first", str(evidence.first),
            "--r0-second", str(evidence.second), "--out", str(evidence.out)]
    for candidate in candidates:
        args.extend(["--candidate", *map(str, candidate)])
    return report.main(args, repo=evidence.command.repo)


def change_record(path, mutate):
    """REWRITE-RECORD: re-seal a synthetic record to reach semantic validation."""
    record = json.loads((path / "run.json").read_text())
    mutate(record)
    (path / "run.json").write_text(json.dumps(record))
    mf.build_tree(path, "evaluation", "ste-rewriter-run").save(
        path.with_name(path.name + ".manifest.json"))


def candidate(evidence, trained):
    """REWRITE-CONFIG: create a score from a verified synthetic epoch producer."""
    command = evidence.command
    command.out = evidence.first.parent / "candidate"
    assert score_adapter(command, trained) == 0
    return command.out, trained.adapter, command.build


def test_rewrite_report_retains_verified_r0_spread(evidence):
    """REWRITE-SPREAD, REWRITE-RECORD: report both rates and all saved breakdowns."""
    assert compare(evidence) == 0
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert result["status"] == "completed" and result["denominator"] == 100
    assert result["r0_first"]["passed"] == 90 and result["r0_second"]["passed"] == 89
    assert result["decode_difference"] == 0.01 and result["comparisons"] == {}
    assert result["meaning_review"] == "pending"
    for label, path in (("r0_first", evidence.first), ("r0_second", evidence.second)):
        original = json.loads((path / "run.json").read_text())
        kept = result["runs"][label]
        assert kept["checks"] == original["checks"]
        assert kept["output_lengths"] == original["output_lengths"]
        assert kept["identity"]["run_sha256"] == mf.sha256_file(path / "run.json")
        assert kept["checks"]["by_kind"]["final"] == {"passed": 10, "total": 10}
        assert kept["checks"]["by_bin"]["2"] == {"passed": 33, "total": 33}
    manifest = mf.load_tree(evidence.out.with_name("comparison.manifest.json"))
    assert mf.verify_tree(manifest, evidence.out) == []
    before = (evidence.out / "comparison.json").read_bytes()
    assert compare(evidence) == 2
    assert (evidence.out / "comparison.json").read_bytes() == before


def test_rewrite_report_pairs_epoch_against_first_r0(evidence, trained):
    """REWRITE-SPREAD: use the first R0 and preserve the verified adapter identity."""
    item = candidate(evidence, trained)
    assert compare(evidence, [item]) == 0
    result = json.loads((evidence.out / "comparison.json").read_text())
    value = result["comparisons"]["r1_epoch_1"]
    assert value["reference"] == "r0_first" and value["candidate"]["passed"] == 100
    assert value["comparison"]["b"] == 10 and value["comparison"]["c"] == 0
    assert value["comparison"]["decode_difference"] == 0.01
    assert value["comparison"]["result"] == "higher"
    assert result["runs"]["r1_epoch_1"]["adapter"]["tree_hash"] == (
        trained.manifest.tree_hash)
    assert "| r1_epoch_1 |" in (evidence.out / "report.md").read_text()


@pytest.mark.parametrize("change, expected", [
    ("failed", "REWRITE-RECORD: comparisons require completed score records"),
    ("adapter", "REWRITE-SPREAD: R0 must be untrained"),
    ("epoch", "REWRITE-SPREAD: R0 must be untrained"),
    ("environment", "REWRITE-SPREAD: score identities differ from the frozen plan"),
    ("plan", "REWRITE-SPREAD: score identities differ from the frozen plan"),
    ("model", "RENDER-OUTPUT: tree_hash differs"),
    ("prompt", "REWRITE-SPREAD: score identities differ from the frozen plan"),
    ("sampler", "REWRITE-SPREAD: score identities differ from the frozen plan"),
    ("code", "REWRITE-SPREAD: score identities differ from the frozen plan"),
    ("gpus", "REWRITE-SPREAD: R0 code and GPU identities must be identical"),
    ("ids", "REWRITE-KIT: the score must cover all 100 frozen inputs"),
    ("summary", "REWRITE-RECORD: run summary differs from retained checker records"),
    ("lengths", "REWRITE-RECORD: run summary differs from retained checker records"),
    ("wall", "REWRITE-RECORD: a finite nonnegative wall time is required"),
    ("memory", "REWRITE-RECORD: both GPU identities and peaks are required"),
])
def test_rewrite_report_rejects_mismatched_r0(evidence, change, expected):
    """REWRITE-SPREAD: valid file hashes cannot authorize changed run identities."""
    def mutate(record):
        """REWRITE-RECORD: change one retained claim while keeping valid JSON."""
        if change == "failed":
            record["status"] = "failed"
        elif change == "adapter":
            record["adapter"] = {"tree_hash": "another"}
        elif change == "epoch":
            record["epoch"] = 1
        elif change == "environment":
            record["environment_sha256"] = "another"
        elif change == "plan":
            record["plan_tree_hash"] = "another"
        elif change == "model":
            record["tree_hash"] = "another"
        elif change == "prompt":
            record["folded_prompt"] = "another"
        elif change == "sampler":
            record["sampler"]["do_sample"] = True
        elif change == "code":
            record["commit"]["rev"] = "another"
        elif change == "gpus":
            record["gpus"]["0"]["name"] = "Another GPU"
        elif change == "ids":
            record["completed_ids"].pop()
        elif change == "summary":
            record["checks"]["passed"] += 1
        elif change == "wall":
            record["wall_seconds"] = -1
        elif change == "memory":
            record["peak_memory_bytes"].pop("1")
        else:
            record["output_lengths"][0]["tokens"] += 1

    change_record(evidence.second, mutate)
    assert compare(evidence) == 2
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


@pytest.mark.parametrize("change, expected", [
    ("repeated", "REWRITE-SPREAD: two distinct R0 runs are required"),
    ("reversed", "REWRITE-SPREAD: the first R0 must precede the second"),
])
def test_rewrite_report_requires_distinct_r0_in_order(evidence, change, expected):
    """REWRITE-SPREAD: a repeated or later baseline is not the first R0 pass."""
    if change == "repeated":
        evidence.second = evidence.first
    else:
        evidence.first, evidence.second = evidence.second, evidence.first
    assert compare(evidence) == 2
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


def test_rewrite_report_reproduces_checker_from_raw_outputs(evidence):
    """REWRITE-CHECK: re-sealed checker text must still match the frozen scorer."""
    root = evidence.second / "checks"
    path = root / "checks.jsonl"
    checks = report.rows(path.read_bytes())
    checks[-1]["check"]["ok"] = False
    path.write_bytes(score.jsonl_bytes(checks))
    mf.build_tree(root, "evaluation", "ste-rewriter-check").save(
        root.with_name("checks.manifest.json"))
    change_record(evidence.second, lambda record: None)
    assert compare(evidence) == 2
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert "frozen scorer" in result["failure"]["message"]


@pytest.mark.parametrize("change, expected", [
    ("wrong_epoch", "REWRITE-CONFIG: requested epoch does not identify this adapter"),
    ("producer", "REWRITE-SPREAD: score adapter identity differs from its producer"),
    ("repeated", "REWRITE-SPREAD: candidate procedure or run identity repeats"),
])
def test_rewrite_report_rejects_bad_candidate(evidence, trained, change, expected):
    """REWRITE-SPREAD: verify epoch provenance again before paired calculations."""
    item = candidate(evidence, trained)
    candidates = [item]
    if change == "wrong_epoch":
        change_record(item[0], lambda record: record.update(epoch=2))
    elif change == "producer":
        trained.producer.run_id = "changed"
        trained.producer.save(trained.producer_path)
    else:
        candidates.append(item)
    assert compare(evidence, candidates) == 2
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


def test_rewrite_report_preserves_input_artifacts(evidence):
    """REWRITE-RECORD: report output cannot contaminate a verified score tree."""
    before = mf.build_tree(evidence.first, "evaluation", "fixture").tree_hash
    evidence.out = evidence.first / "comparison"
    assert compare(evidence) == 2
    assert not evidence.out.exists()
    assert mf.build_tree(evidence.first, "evaluation", "fixture").tree_hash == before


def test_rewrite_report_rejects_source_change(evidence, monkeypatch):
    """REWRITE-RECORD: recheck earlier inputs after all comparison sources are read."""
    original = report.read_score

    def changed_source(path, *args, **kwargs):
        """REWRITE-RECORD: change the first source while the second is read."""
        result = original(path, *args, **kwargs)
        if path == evidence.second:
            change_record(evidence.first, lambda record: record.update(wall_seconds=2))
        return result

    monkeypatch.setattr(report, "read_score", changed_source)
    assert compare(evidence) == 2
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert "artifact changed" in result["failure"]["message"]
    assert not evidence.out.with_name("comparison.manifest.json").exists()


def test_rewrite_report_rejects_producer_change(evidence, trained, monkeypatch):
    """REWRITE-RECORD: recheck the producer after reproducing the checker output."""
    item = candidate(evidence, trained)
    original = score.check_outputs

    def changed_producer(*args, **kwargs):
        """REWRITE-RECORD: change the producer during the isolated reader check."""
        result = original(*args, **kwargs)
        trained.producer.run_id = "changed"
        trained.producer.save(trained.producer_path)
        return result

    monkeypatch.setattr(score, "check_outputs", changed_producer)
    with pytest.raises(score.ScoreError, match="producer"):
        report.read_score(item[0], score.read_plan(evidence.command.plan,
            repo=evidence.command.repo), adapter=item[1], training_build=item[2],
            repo=evidence.command.repo)


@pytest.mark.parametrize("which", ["both-r0", "candidate"])
def test_rewrite_report_binds_score_code_to_plan(evidence, trained, which):
    """REWRITE-ORDER, REWRITE-SPREAD: equal run claims must match the frozen code."""
    candidates = []
    if which == "candidate":
        item = candidate(evidence, trained)
        candidates.append(item)
        paths = [item[0]]
    else:
        paths = [evidence.first, evidence.second]
    for path in paths:
        change_record(path, lambda record: record["commit"].update(rev="other-code"))
    assert compare(evidence, candidates) == 2
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert "score identities differ from the frozen plan" in (
        result["failure"]["message"])
    assert not evidence.out.with_name("comparison.manifest.json").exists()


def test_rewrite_report_does_not_certify_recorded_loop_claims(evidence):
    """REWRITE-RECONCILE: a checker comparison cannot verify seed or token equality."""
    change_record(evidence.first, lambda record: record.update(loop_checks={
        "seed_check_complete": True, "joint_token_check_complete": True}))
    assert compare(evidence) == 0
    result = json.loads((evidence.out / "comparison.json").read_text())
    assert all("loop_checks" not in run for run in result["runs"].values())
    assert json.loads((evidence.first / "run.json").read_text())["loop_checks"] == {
        "seed_check_complete": True, "joint_token_check_complete": True}
    assert "require their separate evidence record" in (
        evidence.out / "report.md").read_text()


@pytest.mark.parametrize("reference", ["first", "second"])
@pytest.mark.parametrize("with_artifacts", [False, True])
def test_rewrite_report_rejects_epoch_reference(
        evidence, trained, monkeypatch, capsys, reference, with_artifacts):
    """REWRITE-SPREAD: even a verified epoch score cannot serve as either R0."""
    item = candidate(evidence, trained)
    setattr(evidence, reference, item[0])
    if with_artifacts:
        original = report.read_score

        def read_with_producer(path, bundle, **kwargs):
            """REWRITE-SPREAD: verify the epoch before the reference-only guard."""
            if path == item[0]:
                kwargs.update(adapter=item[1], training_build=item[2])
            return original(path, bundle, **kwargs)

        monkeypatch.setattr(report, "read_score", read_with_producer)
    expected = ("REWRITE-SPREAD: both references must be untrained R0"
                if with_artifacts else
                "REWRITE-SPREAD: adapter comparisons need the producer artifacts")
    assert compare(evidence) == 2
    assert expected in capsys.readouterr().err
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


@pytest.mark.parametrize("artifact, field, expected", [
    ("score", "kind", "REWRITE-RECORD: expected a rewriter score manifest"),
    ("score", "name", "REWRITE-RECORD: expected a rewriter score manifest"),
    ("checks", "kind", "REWRITE-CHECK: expected the frozen checker artifact"),
    ("checks", "name", "REWRITE-CHECK: expected the frozen checker artifact"),
    ("adapter", "kind", "REWRITE-DECODE: model adapter required"),
])
def test_rewrite_report_rejects_resealed_artifact_kind(
        evidence, trained, capsys, artifact, field, expected):
    """REWRITE-RECORD: valid content hashes do not establish artifact kinds."""
    item = candidate(evidence, trained) if artifact == "adapter" else None
    root = (item[1] if item else evidence.second / "checks" if artifact == "checks"
            else evidence.second)
    path = root.with_name(root.name + ".manifest.json")
    manifest = mf.load_tree(path)
    setattr(manifest, field, "other")
    manifest.save(path)
    if item:
        change_record(item[0], lambda record: record["adapter"].update(
            manifest_sha256=mf.sha256_file(path)))
    elif artifact == "checks":
        change_record(evidence.second, lambda record: None)
    assert compare(evidence, [item] if item else []) == 2
    assert expected in capsys.readouterr().err
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


@pytest.mark.parametrize("change, expected", [
    (lambda r: r.update(run_id=""), "a score run identity is required"),
    (lambda r: r.update(run_id=1), "a score run identity is required"),
    (lambda r: r.update(created_at=r["created_at"].split("+")[0]),
     "score time needs a UTC offset"),
    (lambda r: r["gpus"].pop("1"), "both GPU identities and peaks are required"),
    (lambda r: r["gpus"].update(extra={}),
     "both GPU identities and peaks are required"),
    (lambda r: r["peak_memory_bytes"].pop("1"),
     "both GPU identities and peaks are required"),
    (lambda r: r["peak_memory_bytes"].update(extra={}),
     "both GPU identities and peaks are required"),
])
def test_rewrite_report_rejects_record_shape(evidence, capsys, change, expected):
    """REWRITE-RECORD: each malformed field stops on its own diagnostic."""
    change_record(evidence.second, change)
    assert compare(evidence) == 2
    assert "REWRITE-RECORD: " + expected in capsys.readouterr().err


@pytest.mark.parametrize("gpu", ["0", "1"])
@pytest.mark.parametrize("key", ["allocated", "reserved"])
@pytest.mark.parametrize("value", [-1, 0.5, True])
def test_rewrite_report_rejects_invalid_memory_peak(evidence, capsys, gpu, key, value):
    """REWRITE-RECORD: every peak must be an integer and nonnegative."""
    change_record(evidence.second, lambda r: r["peak_memory_bytes"][gpu].update(
        {key: value}))
    assert compare(evidence) == 2
    assert "REWRITE-RECORD: nonnegative integer memory peaks required" in (
        capsys.readouterr().err)


@pytest.mark.parametrize("epoch", [0.0, False])
def test_rewrite_report_rejects_noninteger_epoch(evidence, capsys, epoch):
    """REWRITE-CONFIG: equality to zero does not make an epoch an integer."""
    change_record(evidence.second, lambda record: record.update(epoch=epoch))
    assert compare(evidence) == 2
    assert "REWRITE-CONFIG: integer epoch required" in capsys.readouterr().err


def test_rewrite_report_rechecks_plan_before_seal(evidence, monkeypatch, capsys):
    """REWRITE-RECORD: a changed, resealed plan leaves failed comparison evidence."""
    original = report.read_score

    def change_plan(path, *args, **kwargs):
        """REWRITE-RECORD: change plan identity after the second run is read."""
        result = original(path, *args, **kwargs)
        if path == evidence.second:
            plan = evidence.command.plan
            (plan / "added-evidence.txt").write_text("new evidence\n")
            mf.build_tree(plan, "evaluation", "ste-rewriter-plan").save(
                plan.with_name(plan.name + ".manifest.json"))
        return result

    monkeypatch.setattr(report, "read_score", change_plan)
    assert compare(evidence) == 2
    expected = "REWRITE-RECORD: frozen plan changed during comparison"
    assert expected in capsys.readouterr().err
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    assert result["status"] == "failed" and result["failure"]["message"] == expected
    assert not evidence.out.with_name("comparison.manifest.json").exists()


@pytest.mark.parametrize("text", ["", " \t\n "])
def test_rewrite_report_rescores_empty_as_missing(evidence, monkeypatch, text):
    """REWRITE-CHECK: verified comparisons retain each pass's empty-output count."""
    def decode(loaded, kit, folded, **kwargs):
        """REWRITE-CHECK: retain a synthetic blank first response in the new run."""
        outputs = outputs_for(kit)
        outputs[0]["output"] = text
        yield from outputs

    monkeypatch.setattr(score, "decode_outputs", decode)
    evidence.command.out = evidence.first.parent / "empty-run"
    assert run(evidence.command) == 0
    evidence.second = evidence.command.out
    assert compare(evidence) == 0
    result = json.loads((evidence.out / "comparison.json").read_bytes())
    checked = result["runs"]["r0_second"]["checks"]
    assert checked["passed"] == 99 and checked["denominator"] == 100
    assert checked["empty_outputs"] == 1 and checked["failure_rules"] == {"missing": 1}
    bundle = score.read_plan(evidence.command.plan, repo=evidence.command.repo)
    verified = report.read_score(evidence.second, bundle, repo=evidence.command.repo)
    assert verified["checks"][0]["check"]["hard"] == {"missing": [True]}
    assert verified["checks"][0]["raw"]["output"] == text
    assert "| r0_second | 99/100 | 1 |" in (evidence.out / "report.md").read_text()
