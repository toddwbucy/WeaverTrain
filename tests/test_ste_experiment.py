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


def test_altered_literals():
    text = "If [[C1]]=[[C2]] then 1.[[C3]] stays."
    assert exp.altered_literals("If [[C1]]=[[C2]], 1.[[C3]] stays.", text) == []
    assert exp.altered_literals("If [[C1]] is [[C2]], 1. with [[C3]] stays.", text) == \
        ["[[C1]]=[[C2]]", "1.[[C3]]"]
    assert exp.altered_literals(None, text) == ["[[C1]]=[[C2]]", "1.[[C3]]"]
