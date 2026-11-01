"""Every rewrite in ste/thesaurus.toml must pass scripts/ste_check.py and use no
unapproved word. Needs the local lexicon (var/ste/approved-verbs.json, made by
scripts/ste_extract.py from the operator's copy of the standard); skipped without it."""

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEXICON = REPO / "var" / "ste" / "approved-verbs.json"
pytestmark = pytest.mark.skipif(not LEXICON.exists(), reason="local STE lexicon not built")

spec = importlib.util.spec_from_file_location("ste_check", REPO / "scripts" / "ste_check.py")
ste = importlib.util.module_from_spec(spec)
sys.modules["ste_check"] = ste
spec.loader.exec_module(ste)

ENTRIES = [(verb, s) for verb, e in tomllib.loads((REPO / "ste" / "thesaurus.toml").read_text()).items()
           for s in e["senses"]]


@pytest.mark.parametrize("verb,sense", ENTRIES, ids=[f"{v}-{i}" for i, (v, _) in enumerate(ENTRIES)])
def test_rewrite_is_clean_ste(verb, sense):
    r = ste.check(sense["after"], sense["before"], ste.Lexicon.load())
    assert r["hard"] == {}, (verb, sense["after"], r["hard"])
    assert "unapproved_words" not in r["soft"], (verb, sense["after"], r["soft"])
