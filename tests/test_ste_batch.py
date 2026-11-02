"""scripts/ste_batch.py: parts keep trajectories whole, the run loop judges and repairs,
stops at the budget and on a substitution script, and assemble restores the code.
The run tests use a fake opencode and need the local lexicon (skipped without it)."""

import importlib.util
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ste_batch", SCRIPTS / "ste_batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)

LEXICON = REPO / "var" / "ste" / "approved-verbs.json"
PROMPT = REPO / "var" / "ste" / batch.DEFAULT_PROMPT
needs_lexicon = pytest.mark.skipif(not (LEXICON.exists() and PROMPT.exists()), reason="local STE lexicon not built")


def row(uuid, texts, final):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "<issue_description>\nbug\n</issue_description>"}]
    for t in texts:
        msgs.append({"role": "assistant", "content": t, "tool_calls": []})
        msgs.append({"role": "tool", "content": "out"})
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "finish", "arguments": json.dumps({"message": final})}}]})
    return {"uuid": uuid, "messages": msgs}


@pytest.fixture
def world(tmp_path, monkeypatch):
    rows = [row("u1", ["Let me check `a.py` now.", "```\nls\n```"], "I fixed `f` in `a.py`."),
            row("u2", ["Let me check `b.py` now."], "I fixed `g` in `b.py`.")]
    data = tmp_path / "data.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(batch, "dataset_file", lambda ds: (data, {"revision": "r0"}))
    monkeypatch.setattr(batch, "var_dir", lambda name: tmp_path / "var")
    monkeypatch.setattr(batch, "exp_dir", lambda name: tmp_path / "exp")
    db = tmp_path / "oc.db"
    con = sqlite3.connect(db)
    con.execute("create table session_v2 (directory text, cost real)")
    con.commit()
    con.close()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    src = []
    for t, r in enumerate(rows, 1):
        segs, why = batch.agent_segments(r, 6000)
        assert why == ""
        src += [{"id": f"T{t:03d}.{s:03d}", "traj": f"T{t:03d}", "row": t - 1, "uuid": r["uuid"], **g}
                for s, g in enumerate(segs, 1)]
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "source.jsonl").write_text("".join(json.dumps(x) + "\n" for x in src))
    return SimpleNamespace(tmp=tmp_path, db=db, rows=rows)


def fake_opencode(tmp_path, outputs: dict, extra_script: str = "") -> str:
    """A stand-in for opencode run: writes roundN.jsonl from OUTPUTS[N] and records its cost."""
    (tmp_path / "outputs.json").write_text(json.dumps(outputs))
    p = tmp_path / "opencode"
    p.write_text(f"""#!{sys.executable}
import json, os, re, sqlite3, sys
msg = sys.argv[-1]
n = 1 if msg.startswith("Read PROMPT") else int(re.search(r"write round(\\d+)", msg).group(1))
outs = json.load(open({str(tmp_path / 'outputs.json')!r}))[str(n)]
src = "input.jsonl" if n == 1 else f"repair-{{n - 1}}.jsonl"
ids = [json.loads(l)["id"] for l in open(src) if l.strip()]
with open(f"round{{n}}.jsonl", "w") as f:
    for i in ids:
        f.write(json.dumps({{"id": i, "output": outs[i]}}) + "\\n")
{extra_script}
con = sqlite3.connect(os.environ["OPENCODE_DB"])
con.execute("insert into session_v2 values (?, 1.0)", (os.getcwd(),))
con.commit()
""")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def args(**kw):
    base = dict(name="t", dataset="d", model="m", budget=10.0, parts=None, limit=None, repairs=2, timeout=60, jobs=1,
                part_size=2, prompt=batch.DEFAULT_PROMPT)
    base.update(kw)
    return SimpleNamespace(**base)


def test_agent_segments_skip_code_only_and_need_a_final_report():
    segs, why = batch.agent_segments(row("u", ["Let me look.", "```\nls\n```"], "Done."), 6000)
    assert [(s["kind"], s["prose"]) for s in segs] == [("narration", True), ("narration", False), ("final", True)]
    r = row("u", ["Let me look."], "Done.")
    r["messages"].pop()
    assert batch.agent_segments(r, 6000) == (None, "no_final_report")
    assert batch.agent_segments(row("u", ["x " * 4000], "Done."), 6000) == (None, "segment_too_long")


@needs_lexicon
def test_parts_keep_trajectories_whole(world):
    batch.cmd_prepare(args(part_size=2))
    parts = sorted((world.tmp / "exp").glob("p*"))
    assert [json.loads((p / "part.json").read_text())["trajectories"] for p in parts] == [["T001"], ["T002"]]
    # the code-only segment is not sent; its code is kept for assembly
    assert [r["id"] for r in batch.read_jsonl(parts[0] / "input.jsonl")] == ["T001.001", "T001.003"]
    assert len(batch.read_jsonl(world.tmp / "var" / "codes.jsonl")) == 5
    # every part denies shell, web and other directories, so --auto cannot approve them
    assert json.loads((parts[0] / "opencode.json").read_text())["permission"]["bash"] == "deny"
    assert "no shell" in (parts[0] / "PROMPT.md").read_text()


@needs_lexicon
def test_run_repairs_then_assemble_restores_code(world, monkeypatch):
    batch.cmd_prepare(args(part_size=2))
    good = {"T001.001": "I will examine [[C1]] now.", "T001.003": "I repaired [[C1]] in [[C2]].",
            "T002.001": "I will examine [[C1]] now.", "T002.002": "I repaired [[C1]] in [[C2]]."}
    bad = dict(good, **{"T001.001": "I am utilizing [[C1]] now."})
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(world.tmp, {"1": bad, "2": good}))
    assert batch.cmd_run(args()) == 0
    p1 = json.loads((world.tmp / "exp" / "p001" / "part.json").read_text())
    assert p1["state"] == "done" and p1["rounds"]["1"]["pass"] == 1 and p1["rounds"]["2"]["pass"] == 2
    assert p1["cost_usd"] == 2.0
    batch.cmd_assemble(args())
    conv = batch.read_jsonl(world.tmp / "var" / "converted.jsonl")
    assert len(conv) == 2
    msgs = conv[0]["messages"]
    assert msgs[2]["content"] == "I will examine `a.py` now."
    assert msgs[4]["content"] == "```\nls\n```"          # code-only: byte-identical
    assert json.loads(msgs[6]["tool_calls"][0]["function"]["arguments"])["message"] == "I repaired `f` in `a.py`."
    assert msgs[1] == world.rows[0]["messages"][1]       # issue text untouched


@needs_lexicon
def test_budget_stops_before_a_session(world, monkeypatch):
    batch.cmd_prepare(args(part_size=2))
    con = sqlite3.connect(world.db)
    con.execute("insert into session_v2 values (?, 10.0)", (str(world.tmp / "exp" / "earlier"),))
    con.commit()
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(world.tmp, {}))
    assert batch.cmd_run(args(budget=10.0)) == 2
    assert not (world.tmp / "exp" / "p001" / "round1.jsonl").exists()


@needs_lexicon
def test_substitution_script_stops_the_run(world, monkeypatch):
    batch.cmd_prepare(args(part_size=2))
    good = {"T001.001": "I will examine [[C1]] now.", "T001.003": "I repaired [[C1]] in [[C2]].",
            "T002.001": "I will examine [[C1]] now.", "T002.002": "I repaired [[C1]] in [[C2]]."}
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(
        world.tmp, {"1": good}, extra_script="open('fix.py', 'w').write('import re\\nre.sub(1, 2, 3)\\n')"))
    assert batch.cmd_run(args()) == 2
    assert "fix.py" in json.loads((world.tmp / "exp" / "p001" / "part.json").read_text())["state"]
    assert not (world.tmp / "exp" / "p002" / "round1.jsonl").exists()


@needs_lexicon
def test_budget_pause_resumes(world, monkeypatch):
    batch.cmd_prepare(args(part_size=2))
    good = {"T001.001": "I will examine [[C1]] now.", "T001.003": "I repaired [[C1]] in [[C2]].",
            "T002.001": "I will examine [[C1]] now.", "T002.002": "I repaired [[C1]] in [[C2]]."}
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(world.tmp, {"1": good}))
    assert batch.cmd_run(args(budget=1.0)) == 2          # p001 spends $1, p002 pauses
    assert json.loads((world.tmp / "exp" / "p002" / "part.json").read_text())["state"].startswith("paused")
    assert batch.cmd_run(args(budget=5.0, jobs=2)) == 0
    assert json.loads((world.tmp / "exp" / "p002" / "part.json").read_text())["state"] == "done"


def test_sample_drops_rows_over_the_token_limit(world, monkeypatch):
    # the trainer's sequence length: a row longer than --max-tokens is not used
    monkeypatch.setattr(batch, "TokenCounter", lambda model: lambda r: 50000 if r["uuid"] == "u1" else 1000)
    monkeypatch.setattr(batch, "var_dir", lambda name: world.tmp / "var2")
    a = SimpleNamespace(name="t", dataset="d", trajectories=1, seed=1, max_chars=6000, exclude=[],
                        max_tokens=32768, model="m", read_factor=2)
    assert batch.cmd_sample(a) == 0
    meta = json.loads((world.tmp / "var2" / "sample.json").read_text())
    assert {r["uuid"] for r in batch.read_jsonl(world.tmp / "var2" / "source.jsonl")} == {"u2"}
    assert meta["skipped"].get("over_max_tokens", 0) <= 1


@needs_lexicon
def test_no_session_without_the_permission_policy(world, monkeypatch):
    batch.cmd_prepare(args(part_size=2))
    (world.tmp / "exp" / "p001" / "opencode.json").write_text("{}")
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(world.tmp, {}))
    assert batch.cmd_run(args()) == 2
    assert not (world.tmp / "exp" / "p001" / "round1.jsonl").exists()


def test_no_opencode_database_costs_nothing(tmp_path, monkeypatch):
    # a batch run only through the Gemini API never made an opencode database
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "absent.db"))
    assert batch.spent(tmp_path) == 0.0


def test_session_timeout_is_a_failed_session(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / "opencode.json").write_text(json.dumps(batch.PERMISSIONS))

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw["timeout"])
    monkeypatch.setattr(batch.subprocess, "run", slow)
    monkeypatch.setattr(batch, "opencode_bin", lambda: "opencode")     # opencode need not be installed
    assert batch.session(tmp_path, "m", "msg", "r1", 5) == -1
    assert "timed out after 5s" in (tmp_path / "logs" / "r1.log").read_text()


@needs_lexicon
def test_changed_literal_goes_back_to_repair(tmp_path):
    judge = batch.exp.Judge(batch.DEFAULT_PROMPT)
    inputs = [{"id": "s1", "kind": "narration", "text": "I will examine 1.[[C1]] now."}]
    (tmp_path / "round1.jsonl").write_text(json.dumps({"id": "s1", "output": "I will examine 1. [[C1]] now."}) + "\n")
    ok, fails = batch.judge_round(tmp_path, inputs, judge)
    assert ok == 0 and "1.[[C1]]" in fails[0]["problems"]


@needs_lexicon
def test_assemble_drops_a_row_that_the_rewrites_made_too_long(world, monkeypatch):
    # PR #4 review: the token limit is checked again on the rewritten row
    batch.cmd_prepare(args(part_size=2))
    good = {"T001.001": "I will examine [[C1]] now.", "T001.003": "I repaired [[C1]] in [[C2]].",
            "T002.001": "I will examine [[C1]] now.", "T002.002": "I repaired [[C1]] in [[C2]]."}
    monkeypatch.setenv("OPENCODE_BIN", fake_opencode(world.tmp, {"1": good}))
    assert batch.cmd_run(args()) == 0
    (world.tmp / "var" / "sample.json").write_text(json.dumps({"max_tokens": 1000, "model": "sampled-model"}))
    used = []
    monkeypatch.setattr(batch, "TokenCounter",
                        lambda model: used.append(model) or (lambda r: 2000 if r["uuid"] == "u2" else 500))
    batch.cmd_assemble(args(model=None))
    assert used == ["sampled-model"]       # the tokenizer the sample measured with
    assert [r["uuid"] for r in batch.read_jsonl(world.tmp / "var" / "converted.jsonl")] == ["u1"]
    failed = batch.read_jsonl(world.tmp / "var" / "failed.jsonl")
    assert failed == [{"traj": "T002", "row": 1, "uuid": "u2", "failed": [], "over_max_tokens": 2000}]
    run = json.loads((world.tmp / "var" / "run.json").read_text())
    assert run["converted"] == 1 and run["over_max_tokens"] == 1 and run["max_tokens"] == 1000
