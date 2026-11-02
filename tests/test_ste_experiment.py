"""The literal check in scripts/ste_experiment.py score: a token glued to a placeholder
must come back unchanged."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ste_experiment", SCRIPTS / "ste_experiment.py")
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)


def test_glued_tokens_are_literals_and_edges_are_not():
    text = "See 145.[[C8]] and **[[C2]]**, then [[C3]]. Run [[C4]]([[C5]] now."
    assert exp.glued_literals(text) == ["145.[[C8]]", "[[C4]]([[C5]]"]


def test_placeholders_joined_by_a_slash_are_not_a_literal():
    # "[[C8]]/[[C9]] parameters" may become "[[C8]] and [[C9]]": every placeholder stays,
    # and the placeholder check counts each one
    text = "Keep [[C8]]/[[C9]] and v1/[[C3]] as they are."
    assert exp.glued_literals(text) == ["v1/[[C3]]"]


def test_altered_literals():
    text = "If [[C1]]=[[C2]] then 1.[[C3]] stays."
    assert exp.altered_literals("If [[C1]]=[[C2]], 1.[[C3]] stays.", text) == []
    assert exp.altered_literals("If [[C1]] is [[C2]], 1. with [[C3]] stays.", text) == \
        ["[[C1]]=[[C2]]", "1.[[C3]]"]
    assert exp.altered_literals(None, text) == ["[[C1]]=[[C2]]", "1.[[C3]]"]


def test_incomplete_round_is_reported(tmp_path):
    (tmp_path / "repair-1.jsonl").write_text('{"id": "s1"}\n{"id": "s2"}\n')
    (tmp_path / "round2.jsonl").write_text('{"id": "s1", "output": "x"}\n')
    assert exp.incomplete(tmp_path, 2) == (1, 2)
    (tmp_path / "round2.jsonl").write_text('{"id": "s1", "output": "x"}\n{"id": "s2", "output": "y"}\n')
    assert exp.incomplete(tmp_path, 2) is None
    (tmp_path / "input.jsonl").write_text('{"id": "s1"}\n{"id": "s2"}\n{"id": "s3"}\n')
    (tmp_path / "round1.jsonl").write_text('{"id": "s1", "output": "x"}\n{"id": "s3", "output": "z"}\n')
    assert exp.incomplete(tmp_path, 1) == (2, 3)


def test_substitution_scripts_and_near_copies(tmp_path):
    (tmp_path / "make.py").write_text("import json\nprint(json.dumps({'id': 'k1'}))\n")
    assert exp.substitution_scripts(tmp_path) == []
    (tmp_path / "fix.py").write_text("import re\ns = re.sub(r'\\bfix\\b', 'change', s)\n")
    assert exp.substitution_scripts(tmp_path) == ["fix.py"]
    inputs = [{"id": "a", "text": "Let me fix the parser now."}, {"id": "b", "text": "Let me fix it."}]
    assert exp.near_copies({"a": "Let me fix the parser now!", "b": "I will repair it."}, inputs) == ["a"]
    # with a judge, an input that already passes may come back unchanged: not a copy
    passes = lambda out, text: {"ok": text.startswith("Let me fix the parser")}
    assert exp.near_copies({"a": "Let me fix the parser now!"}, inputs, judge=passes) == []
    fails = lambda out, text: {"ok": False}
    assert exp.near_copies({"a": "Let me fix the parser now!"}, inputs, judge=fails) == ["a"]


def test_malformed_grade_records_are_problems_not_crashes(tmp_path):
    (tmp_path / "grades.jsonl").write_text(
        '{"gid": "g1", "verdict": "Faithful"}\n["not", "an", "object"]\n{"verdict": "minor"}\n')
    out, problems = exp.read_grades(tmp_path, {"g1": {}, "g2": {}})
    assert set(out) == {"g1"} and out["g1"]["verdict"] == "faithful"
    assert any("TypeError" in p for p in problems) and any("KeyError" in p for p in problems)
    assert any(p.startswith("INCOMPLETE") for p in problems)


def test_send_parts_with_nothing_to_send(tmp_path, monkeypatch, capsys):
    # every rewrite passed: repair-1.jsonl is empty, and send-parts says so without a crash
    from types import SimpleNamespace
    d = tmp_path / "m"
    (d / "messages").mkdir(parents=True)
    (d / "round1.jsonl").write_text("")
    (d / "repair-1.jsonl").write_text("")
    monkeypatch.setattr(exp, "models", lambda names: [d])
    assert exp.cmd_send_parts(SimpleNamespace(task="repair", names=["m"], size=10)) == 0
    assert "queued 0 parts" in capsys.readouterr().out
