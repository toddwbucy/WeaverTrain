"""Positive and negative controls for scripts/ste_check.py on a small synthetic lexicon
(no text from ASD-STE100: the words below are ordinary English, chosen for the cases)."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ste_check.py"
spec = importlib.util.spec_from_file_location("ste_check", SCRIPT)
ste = importlib.util.module_from_spec(spec)
sys.modules["ste_check"] = ste  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(ste)

APPROVED = {w: ["x"] for w in (
    "the a an i will is are it this that when if not do does make makes made find examine "
    "error test function in of to and then first only one problem shows show result".split())}
LEX = ste.Lexicon.build(
    approved_pos=APPROVED,
    unapproved={"should": ["v"], "fix": ["v"], "run": ["v"], "test": ["v"], "now": ["adv"], "drop": ["v"]},
    unapproved_verbs={"fix": ["REPAIR (v)"], "run": ["OPERATE (v)"], "test": ["TEST (n)"], "should": [],
                      "drop": ["RELEASE (v)"]},
    technical_verbs=["commit", "roll back", "drag and drop"],
    technical_nouns=["repository", "issue"],
    approved_verbs=["do", "make", "find", "examine", "show"],
)


def run(out, inp="text", **kw):
    return ste.check(out, inp, LEX, **kw)


def test_clean_text_passes():
    r = run("I will examine the function [[C1]]. Then I will make the test.", "a [[C1]] b")
    assert r["ok"], r


def test_each_hard_rule_fails_alone():
    assert run("I will fix the error.")["hard"] == {"verbs": ["fix"]}
    assert run("The test is running.")["hard"] == {"verbs": ["running"]}
    assert run("I tested the function.")["hard"] == {"verbs": ["tested"]}
    assert run("It is the problem; I will examine it.")["hard"] == {"semicolon": [";"]}
    assert "contractions" in run("I'll examine the error.")["hard"]
    assert "contractions" in run("It doesn't show the error.")["hard"]
    assert run("I should examine it.")["hard"] == {"verbs": ["should"]}


def test_nouns_and_technical_words_are_not_verbs():
    # "test" and "tests" are approved as a noun; technical verbs and nouns pass
    assert run("Do the tests. I will commit the changes to the repository.")["hard"] == {}
    assert run("Examine snake_case_name and DataFrame and GIF.")["hard"] == {}


def test_placeholders_must_appear_exactly_once():
    r = run("Examine [[C1]] and [[C1]].", "[[C1]] [[C2]]")
    assert r["hard"]["placeholders"] == [{"missing": ["[[C2]]"], "extra_or_repeated": ["[[C1]]"]}]


def test_sentence_limits_split_instruction_and_description():
    instr = "Examine " + " ".join(["the error"] * 10) + "."          # 21 words, an instruction
    desc = "The error " + " ".join(["is the problem"] * 7) + "."     # 23 words, a description
    assert "length" in run(instr)["hard"]
    assert "length" not in run(desc)["hard"]
    assert "length" in run(desc, limit_description=20)["hard"]


def test_unknown_and_unapproved_words_are_reported_not_failed():
    r = run("Now I will examine the zebra.")
    assert r["ok"] and r["soft"] == {"unapproved_words": ["now"], "unknown_words": ["zebra"]}


def test_repair_note_names_alternatives():
    r = run("I will fix it; it's broken.", "x [[C1]]")
    note = ste.repair_note(r, LEX)
    assert "REPAIR (v)" in note and "[[C1]]" in note and "semicolon" in note


def test_apostrophe_s_contractions_fail():
    for s in ("It's the error.", "That’s the result.", "There's one problem.", "Let's examine it."):
        assert "contractions" in run(s)["hard"], s
    assert "contractions" not in run("The function's result is the error.")["hard"]


def test_multi_word_technical_verb_admits_the_phrase_not_its_parts():
    assert run("I will drag and drop the function. I rolled back the commit.")["hard"] == {}
    assert run("I will drop the function.")["hard"] == {"verbs": ["drop"]}


def test_wrapped_sentence_is_counted_whole():
    long = "Examine " + " ".join(["the error"] * 10) + "."   # 21 words, an instruction
    assert "length" in run(long)["hard"]
    assert "length" in run(long.replace(" ", "\n"))["hard"]
    # real boundaries still split: list items, headings, blank lines
    items = "- Examine the error.\n- Examine the function.\n\n## Result\nThe test shows the error."
    assert ste.sentences(items) == ["Examine the error.", "Examine the function.", "Result",
                                    "The test shows the error."]
