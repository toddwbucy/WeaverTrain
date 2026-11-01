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
