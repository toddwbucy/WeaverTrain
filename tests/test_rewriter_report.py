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


@pytest.mark.parametrize("change", ["failed", "adapter", "epoch", "environment",
                                    "plan", "model", "prompt", "sampler", "code",
                                    "gpus", "ids", "summary", "lengths", "wall",
                                    "memory"])
def test_rewrite_report_rejects_mismatched_r0(evidence, change):
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
    assert result["status"] == "failed" and result["failure"]
    assert not evidence.out.with_name("comparison.manifest.json").exists()


@pytest.mark.parametrize("change", ["repeated", "reversed"])
def test_rewrite_report_requires_distinct_r0_in_order(evidence, change):
    """REWRITE-SPREAD: a repeated or later baseline is not the first R0 pass."""
    if change == "repeated":
        evidence.second = evidence.first
    else:
        evidence.first, evidence.second = evidence.second, evidence.first
    assert compare(evidence) == 2


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


@pytest.mark.parametrize("change", ["wrong_epoch", "producer", "repeated"])
def test_rewrite_report_rejects_bad_candidate(evidence, trained, change):
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
