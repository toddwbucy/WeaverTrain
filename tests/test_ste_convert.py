"""Masking in scripts/ste_convert.py: what the model sees and what comes back."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ste_convert", SCRIPTS / "ste_convert.py")
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)


def test_existing_placeholders_are_not_masked_again():
    text = "Call `a` then `b`, see x[0] and f(y, z)."
    masked, codes = conv.mask(text)
    assert masked == "Call [[C1]] then [[C2]], see [[C3]] and [[C4]]."
    assert codes == ["`a`", "`b`", "x[0]", "f(y, z)"]
    assert conv.unmask(masked, codes) == text


def test_round_trip_with_nested_and_bracketed_spans():
    for text in ("Use `foo` now.", "Set d[`k`] = [1, [2]] in path/to/file.py.",
                 "See {'a': [1]} and \"quoted [x]\" text."):
        masked, codes = conv.mask(text)
        assert "[[[" not in masked, masked
        assert conv.unmask(masked, codes) == text


def test_brackets_inside_string_literals_do_not_end_the_span():
    for text, want in (("Call f('hello)world') now.", "Call [[C1]] now."),
                       ("Use g('a]b', x[0]) here.", "Use [[C1]] here.")):
        masked, codes = conv.mask(text)
        assert masked == want, masked
        assert conv.unmask(masked, codes) == text


def test_apostrophes_in_words_are_not_string_delimiters():
    text = "Use (don't remove it); then check (it's ready)."
    masked, codes = conv.mask(text)
    assert masked == text and codes == []
    masked, codes = conv.mask("Call f(r'a)b', x) now.")
    assert masked == "Call [[C1]] now." and codes == ["f(r'a)b', x)"]


def test_escaped_double_quote_stays_inside_the_string():
    text = 'Call f("a\\"b)world") now.'
    masked, codes = conv.mask(text)
    assert masked == "Call [[C2]] now.", masked
    assert conv.unmask(masked, codes) == text


def test_windows_path_backslash_does_not_swallow_the_next_string():
    text = 'Use "C:\\tmp\\"; then read "file".'
    masked, codes = conv.mask(text)
    assert masked == "Use [[C1]]; then read [[C2]].", masked
    assert conv.unmask(masked, codes) == text


def test_postgres_escape_string_prefix():
    masked, codes = conv.mask("Call length(E'a)b') now.")
    assert masked == "Call [[C1]] now." and codes == ["length(E'a)b')"]


# Excerpts of kit segments s32, s38, s52 and s55 (nvidia/Nemotron-SWE-v1, CC BY 4.0),
# the cases where the 2026-11-01 repair round showed literal text reaching the checker.
S32 = """### Installed Versions\r
\r
<details>\r
commit           : f77480f9f5ed068d7e726862dff2db0411015201\r
\r
python           : 3.10.2.final.0\r
processor        : Intel64 Family 6 Model 158 Stepping 10, GenuineIntel\r
byteorder        : little\r
pandas_datareader: None\r
zstandard        : None\r
\r
</details>"""
S38 = """<details><summary>Examples on 2.0.x</summary>\r
\r
```\r
df = pd.DataFrame([[1, 2], [3, 4]])\r
```\r
\r
</details>"""
S52 = ('3. "Actual behavior: The function raises an OverflowError with the message '
       '"Python int too large to convert to C long" when it attempts to cast the negative '
       'value directly to the unsigned dtype."')
S55 = ("1.2 Technical details:\n- Error: UnicodeEncodeError: 'ascii' codec can't encode "
       "character '\\xff' in position 49: ordinal not in range(128)\n- File type: TIFF format")


def prose(text):
    masked, codes = conv.mask(text)
    assert conv.unmask(masked, codes) == text
    return conv.ste_check.PLACEHOLDER.sub(" ", masked)


def test_installed_versions_listing_and_details_tags_are_masked():
    p = prose(S32)
    assert "Stepping" not in p and "details" not in p and "processor" not in p, p
    assert "Installed Versions" in p  # the heading is prose


def test_summary_tags_are_masked_and_their_prose_stays():
    p = prose(S38)
    assert "details" not in p and "summary" not in p, p
    assert "Examples on" in p


def test_quoted_error_message_inside_a_quotation_is_masked():
    p = prose(S52)
    assert "too large to convert" not in p, p
    assert "Actual behavior" in p  # the quotation around it is prose


def test_exception_message_is_masked_to_the_end_of_the_line():
    p = prose(S55)
    assert "codec" not in p and "ordinal" not in p, p
    assert "Technical details" in p and "TIFF format" in p


def test_prose_labels_are_not_a_listing():
    text = "File: core.py\nLines: 1027-1028\nFunction: the apply method"
    p = prose(text)
    assert "Lines" in p and "Function: the apply method" in p, p


def test_masked_literals_never_reach_the_checker():
    lex = conv.ste_check.Lexicon.build(
        approved_pos={w: ["x"] for w in "the a is in of on and".split()},
        unapproved={"stepping": ["v"], "details": ["v"], "convert": ["v"], "encode": ["v"]},
        unapproved_verbs={}, technical_verbs=[], technical_nouns=[], approved_verbs=[])
    for text in (S32, S38, S52, S55):
        masked, _ = conv.mask(text)
        hard = conv.ste_check.check(masked, masked, lex)["hard"]
        flagged = {w.lower() for rule in ("verbs", "ing") for w in hard.get(rule, [])}
        assert not {"stepping", "details", "convert", "encode"} & flagged, hard


def test_kit_segments_mask_their_literals_when_the_source_is_present():
    import json
    import pytest
    src = Path(__file__).resolve().parents[1] / "var" / "ste" / "kit-r3" / "source.jsonl"
    if not src.exists():
        pytest.skip("local kit source (dataset text) not present")
    rows = {r["id"]: r["raw"] for r in map(json.loads, open(src, encoding="utf-8"))}
    banned = {"s32": ["Stepping", "<details>"], "s38": ["<details>", "<summary>"],
              "s52": ["too large to convert"], "s55": ["can't encode"]}
    for sid, words in banned.items():
        p = prose(rows[sid])
        assert not [w for w in words if w in p], (sid, p[-400:])
