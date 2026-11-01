import json

import pytest

from weaver_train import bench
from weaver_train import manifest as mf

MODEL = {"name": "tiny", "tree_hash": "m" * 64}


@pytest.fixture
def snapshot(tmp_path):
    d = tmp_path / "snap-2026-10-31"
    d.mkdir()
    tasks = [{"task_id": f"t{i}", "answer": i % 2} for i in range(4)]
    (d / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    (d / "README").write_text("frozen fixture\n")
    m = mf.build_tree(d, "snapshot", "fixture-2026-10-31")
    p = tmp_path / "snap.json"
    m.save(p)
    return d, p, m


def parity_runner(task, *, repetition, seed, snapshot_dir):
    return {"status": "resolved" if task["answer"] == 1 else "unresolved", "seed_seen": seed}


def test_repeated_runs_record_per_task(snapshot, tmp_path):
    d, p, m = snapshot
    recs = bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out",
                           repetitions=3, seed=10, sampler={"temperature": 0.0})
    assert [r.repetition for r in recs] == [0, 1, 2]
    assert [r.seed for r in recs] == [10, 11, 12]
    for r in recs:
        assert r.engine == "python-hf" and r.status == "completed"
        assert r.snapshot == {"name": "fixture-2026-10-31", "tree_hash": m.tree_hash}
        assert r.results == {"tasks": 4, "resolved": 2, "unresolved": 2, "error": 0, "resolved_rate": 0.5}
        lines = [json.loads(x) for x in (tmp_path / "out" / f"rep-{r.repetition:02d}" / "results.jsonl")
                 .read_text().splitlines()]
        assert [x["task_id"] for x in lines] == ["t0", "t1", "t2", "t3"]
        assert all(x["seed_seen"] == r.seed and "duration_s" in x for x in lines)
        assert mf.load_run(tmp_path / "out" / f"rep-{r.repetition:02d}" / "run.json") == r


def test_refuses_modified_snapshot(snapshot, tmp_path):
    d, p, _ = snapshot
    (d / "README").write_text("edited\n")
    with pytest.raises(mf.ManifestError, match="README"):
        bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_expected_hash_pin(snapshot, tmp_path):
    d, p, _ = snapshot
    with pytest.raises(bench.BenchError, match="expected"):
        bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "o", expected_hash="0" * 64)


def test_runner_errors_are_recorded_not_fatal(snapshot, tmp_path):
    d, p, _ = snapshot

    def flaky(task, **kw):
        if task["task_id"] == "t1":
            raise RuntimeError("container died")
        if task["task_id"] == "t2":
            return {"status": "maybe"}
        return {"status": "resolved"}

    (rec,) = bench.run_bench(d, p, flaky, model=MODEL, out_dir=tmp_path / "out", runner_name="r:flaky")
    assert rec.results["error"] == 2 and rec.results["resolved"] == 2
    lines = {json.loads(x)["task_id"]: json.loads(x)
             for x in (tmp_path / "out" / "rep-00" / "results.jsonl").read_text().splitlines()}
    assert "container died" in lines["t1"]["error"]
    assert "maybe" in lines["t2"]["error"]


def test_resume_skips_finished_tasks(snapshot, tmp_path):
    d, p, _ = snapshot
    calls = []

    def stops_after_two(task, **kw):
        calls.append(task["task_id"])
        if len(calls) > 2:
            raise KeyboardInterrupt  # simulate the process being stopped
        return {"status": "resolved"}

    with pytest.raises(KeyboardInterrupt):
        bench.run_bench(d, p, stops_after_two, model=MODEL, out_dir=tmp_path / "out", runner_name="r:f")
    calls.clear()
    (rec,) = bench.run_bench(d, p, lambda t, **k: calls.append(t["task_id"]) or {"status": "resolved"},
                             model=MODEL, out_dir=tmp_path / "out", runner_name="r:f")
    assert calls == ["t2", "t3"]
    assert rec.results["tasks"] == 4 and rec.results["resolved"] == 4


def test_refuses_to_mix_runs_in_one_out_dir(snapshot, tmp_path):
    d, p, _ = snapshot
    bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
    with pytest.raises(bench.BenchError, match="different run"):
        bench.run_bench(d, p, parity_runner, model={"name": "other", "tree_hash": "x" * 64},
                        out_dir=tmp_path / "out")


@pytest.mark.parametrize("change", [{"runner_name": "other:runner"}, {"task_ids": ["t0"]}])
def test_resume_refuses_other_runner_or_task_selection(snapshot, tmp_path, change):
    d, p, _ = snapshot
    first = {"runner_name": "r:f", "task_ids": ["t0", "t1"]}
    bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out", **first)
    with pytest.raises(bench.BenchError, match="different run"):
        bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out", **{**first, **change})


def test_unserializable_runner_field_is_recorded(snapshot, tmp_path):
    d, p, _ = snapshot
    (rec,) = bench.run_bench(d, p, lambda t, **k: {"status": "resolved", "where": tmp_path},
                             model=MODEL, out_dir=tmp_path / "out", runner_name="r:f")
    assert rec.results["resolved"] == 4


def test_task_subset_and_bad_tasks(snapshot, tmp_path):
    d, p, _ = snapshot
    (rec,) = bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "o1", task_ids=["t1"])
    assert rec.results["tasks"] == 1
    with pytest.raises(bench.BenchError, match="unknown task"):
        bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "o2", task_ids=["nope"])


def test_duplicate_task_ids_rejected(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "tasks.jsonl").write_text('{"task_id": "a"}\n{"task_id": "a"}\n')
    with pytest.raises(bench.BenchError, match="duplicate"):
        bench.read_tasks(d)


def test_cli(snapshot, tmp_path, capsys):
    d, p, m = snapshot
    rc = bench.main([str(d), str(p), "--runner", "tests.test_bench:parity_runner", "--model-name", "tiny",
                     "--model-hash", "m" * 64, "--repetitions", "2", "--out", str(tmp_path / "cli"),
                     "--sampler", '{"temperature": 0.7}'])
    assert rc == 0
    rec = mf.load_run(tmp_path / "cli" / "rep-01" / "run.json")
    assert rec.sampler == {"temperature": 0.7} and rec.config["runner"] == "tests.test_bench:parity_runner"


def test_default_runner_identity_is_module_qualified():
    assert bench.runner_identity(parity_runner) == f"{__name__}:parity_runner"
    for unnamed in (lambda t, **k: {}, type("R", (), {"__call__": lambda s, t, **k: {}})()):
        with pytest.raises(bench.BenchError, match="runner_name"):
            bench.runner_identity(unnamed)


class Harness:
    @staticmethod
    def run(task, **kw):
        return {"status": "resolved"}


def test_cli_loads_the_identity_it_records():
    ident = bench.runner_identity(Harness.run)
    assert ident == f"{__name__}:Harness.run"
    assert bench.load_runner(ident) is Harness.run


def test_resume_drops_a_partial_last_record(snapshot, tmp_path):
    d, p, _ = snapshot
    bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
    res = tmp_path / "out" / "rep-00" / "results.jsonl"
    lines = res.read_text().splitlines(keepends=True)
    res.write_text("".join(lines[:2]) + lines[2][:10])  # stopped mid-append on task 3
    (rec,) = bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
    assert rec.results["tasks"] == 4 and len(res.read_text().splitlines()) == 4


def test_resume_still_refuses_damage_before_the_last_record(snapshot, tmp_path):
    d, p, _ = snapshot
    bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
    res = tmp_path / "out" / "rep-00" / "results.jsonl"
    lines = res.read_text().splitlines(keepends=True)
    res.write_text(lines[0][:10] + "\n" + "".join(lines[1:]))
    with pytest.raises(json.JSONDecodeError):
        bench.run_bench(d, p, parity_runner, model=MODEL, out_dir=tmp_path / "out")
