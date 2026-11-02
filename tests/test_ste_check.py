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


def test_capitals_are_technical_names_only_when_listed_or_not_words():
    lex = ste.Lexicon.build(approved_pos=APPROVED, unapproved={"should": ["v"]},
                            unapproved_verbs={"should": []}, technical_verbs=[], technical_nouns=[],
                            approved_verbs=["do", "make", "show"], caps_names=["AND", "SELECT"])
    chk = lambda out, inp="text": ste.check(out, inp, lex)["hard"]
    assert chk("The test uses AND. Use SELECT.") == {}            # listed technical names
    assert chk("The PPM file shows the error.") == {}              # acronym, not a word
    assert chk("The test is THE result.") == {"capitals": ["THE"]}  # an approved word in capitals
    assert chk("The RESULT shows the error.", "The RESULT") == {}   # capitals copied from the input
    assert chk("The FIND shows the error.", "The RESULT") == {}     # the source writes words in capitals
    assert chk("The RESULT shows the error.") == {"capitals": ["RESULT"]}
    assert "capitals" in chk("I SHOULD do it.") and "verbs" in chk("I SHOULD do it.")
    assert chk("The result and the error.") == {}                  # the conjunction stays prose


# Round 3 flags (2026-11-01): nouns and adjectives that the checker read as verbs.
NOUN_LEX = ste.Lexicon.build(
    approved_pos={w: ["x"] for w in (
        "the a an i will is are it this that when if not do does make makes made find examine "
        "error value choice code use set to and then result".split())},
    unapproved={"fix": ["v"], "order": ["n", "v"], "design": ["v"], "progress": ["n", "v"],
                "fail": ["v"], "block": ["v"]},
    unapproved_verbs={"fix": ["REPAIR (v)"], "order": ["TELL (v)"], "design": ["HAVE (v)"],
                      "progress": ["CONTINUE (v)"], "fail": [], "block": []},
    technical_verbs=[], technical_nouns=[], approved_verbs=["do", "make", "find", "examine", "use", "set"],
)


def test_adjective_and_noun_uses_are_not_verbs():
    for text in ("Use a fixed value.", "This is a design choice.", "Examine the block."):
        r = ste.check(text, "x", NOUN_LEX)
        assert "verbs" not in r["hard"], (text, r["hard"])
    # known limit: a noun after a modifier ("the if block") still reads as a verb,
    # because one word between would also pass "the code fixes the error"
    assert "block" in ste.check("Examine the if block.", "x", NOUN_LEX)["hard"]["verbs"]


def test_unapproved_noun_is_reported_as_a_word_not_as_a_verb():
    r = ste.check("Do the steps in order.", "x", NOUN_LEX)
    assert "verbs" not in r["hard"] and r["soft"].get("unapproved_words") == ["order"], r
    r = ste.check("I will examine the progress.", "x", NOUN_LEX)
    assert "verbs" not in r["hard"] and "progress" in r["soft"]["unapproved_words"], r


def test_verb_uses_still_fail():
    for text, word in (("I will fix the error.", "fix"), ("This fails.", "fails"),
                       ("Then order the values.", "order"), ("The code fixed the error.", "fixed"),
                       ("I will design a test, then fix it.", "design")):
        r = ste.check(text, "x", NOUN_LEX)
        assert word in r["hard"].get("verbs", []), (text, r["hard"])


def test_punctuation_between_breaks_the_context():
    r = ste.check("Use the code. Fix the error.", "x", NOUN_LEX)
    assert "fix" in r["hard"].get("verbs", []), r


def test_fixed_mapping_replaces_the_standards_alternatives():
    lex = ste.Lexicon.build(approved_pos={"the": ["x"]}, unapproved={"call": ["v"]},
                            unapproved_verbs={"call": ["TELL (v)"]}, technical_verbs=[],
                            technical_nouns=[], approved_verbs=[])
    lex.fixed = {"call": "use"}
    r = ste.check("I call the parser.", "x", lex)
    note = ste.repair_note(r, lex)
    assert 'Write "use" for it' in note and "TELL" not in note, note


def test_inflected_noun_takes_the_part_of_speech_of_its_lemma():
    # PR #4 review: "orders" after "the" was skipped with no finding; "order" was reported
    r = ste.check("Do the orders.", "x", NOUN_LEX)
    assert "verbs" not in r["hard"] and r["soft"].get("unapproved_words") == ["orders"], r


def test_repair_note_names_invented_markup_and_changed_literals():
    note = ste.repair_note({"hard": {"invented_markup": ["<h4>"], "altered_literals": ["1.[[C3]]"]}}, LEX)
    assert "<h4>" in note and "Remove it" in note
    assert "1.[[C3]]" in note and "exactly as it is in the input" in note
