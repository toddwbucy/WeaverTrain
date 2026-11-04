"""Spec 0.1 section 9: paired rewrite scores and the first R0 decode reference."""

import copy
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lora_score
import rewriter_score as score


def source_ids():
    """REWRITE-KIT: synthetic identities for the fixed 100-result denominator."""
    return [f"s{n:03d}" for n in range(100)]


def checks(passed):
    """REWRITE-SPREAD: synthetic checker results, with no generated model text."""
    return [{"id": key, "check": {"ok": n in passed}}
            for n, key in enumerate(source_ids())]


@pytest.mark.parametrize("first, second", [(0, 11), (11, 0), (0, 0), (100, 100)])
def test_rewrite_spread_records_both_rates_and_absolute_difference(first, second):
    """REWRITE-SPREAD: decode difference uses the two rates with denominator 100."""
    result = score.rewrite_spread(source_ids(), checks(range(first)), checks(range(second)))
    assert result == {"denominator": 100,
                      "r0_first": {"passed": first, "rate": first / 100},
                      "r0_second": {"passed": second, "rate": second / 100},
                      "decode_difference": abs(first - second) / 100}


@pytest.mark.parametrize("first, current, verdict, gain, loss", [
    (set(), set(range(10)), "higher", 10, 0),
    (set(range(10)), set(), "lower", 0, 10),
    (set(), set(range(5)), "not shown", 5, 0),
    (set(range(100)), set(range(100)), "flat", 0, 0),
    (set(range(50)), set(range(50, 100)), "not shown", 50, 50),
])
def test_rewrite_spread_keeps_run_margin_results(first, current, verdict, gain, loss):
    """REWRITE-SPREAD, RUN-MARGIN: keep all four labels and the paired outcomes."""
    baseline = checks(first)
    candidate = checks(current)
    result = score.compare_rewrites(source_ids(), baseline, baseline, candidate)
    comparison = result["comparison"]
    assert result["metric"] == "rewrite_ste" and result["reference"] == "r0_first"
    assert comparison["result"] == verdict
    assert (comparison["b"], comparison["c"], comparison["pairs"]) == (gain, loss, 100)
    assert comparison["difference"] == (gain - loss) / 100
    assert comparison["decode_difference"] == 0
    assert "100 segments" in comparison["limit"]
    assert "correct_request" not in result
    expected = lora_score.compare_pairs(
        [n in first for n in range(100)], [n in current for n in range(100)], 0)
    assert comparison == expected
    if gain == 5 and loss == 0:
        assert comparison["p"] == 0.0625
    if verdict == "flat":
        assert -0.05 <= comparison["interval_95"][0] <= comparison["interval_95"][1] <= 0.05
    if gain == loss == 50:
        assert comparison["interval_95"][0] < -0.05
        assert comparison["interval_95"][1] > 0.05


@pytest.mark.parametrize("second_passed, verdict", [(9, "higher"), (10, "not shown"),
                                                    (11, "not shown")])
def test_rewrite_spread_uses_first_r0_and_strict_decode_margin(second_passed, verdict):
    """REWRITE-SPREAD: the second R0 controls noise and never replaces the baseline."""
    result = score.compare_rewrites(
        source_ids(), checks(set()), checks(range(second_passed)), checks(range(10)))
    assert result["r0_first"] == {"passed": 0, "rate": 0}
    assert result["candidate"] == {"passed": 10, "rate": 0.1}
    comparison = result["comparison"]
    assert comparison["p"] < 0.05
    assert comparison["b"] == 10 and comparison["c"] == 0
    assert comparison["decode_difference"] == second_passed / 100
    assert comparison["result"] == verdict


def test_rewrite_spread_uses_count_difference_at_a_nonzero_boundary():
    """REWRITE-SPREAD: rounding two rates cannot turn equality into a larger gain."""
    result = score.compare_rewrites(
        source_ids(), checks(range(20)), checks(range(30)), checks(range(30)))
    assert result["decode_difference"] == result["comparison"]["difference"] == 0.1
    assert result["comparison"]["p"] < 0.05
    assert result["comparison"]["result"] == "not shown"


def test_rewrite_spread_uses_rate_difference_not_changed_verdict_count():
    """REWRITE-SPREAD: equal R0 rates have zero spread even when verdicts differ."""
    result = score.rewrite_spread(source_ids(), checks(range(50)), checks(range(50, 100)))
    assert result["r0_first"] == result["r0_second"] == {"passed": 50, "rate": 0.5}
    assert result["decode_difference"] == 0


def test_rewrite_spread_pairs_by_identity_and_keeps_rows_unchanged():
    """REWRITE-SPREAD: shuffled record order cannot change pair direction or counts."""
    ids = source_ids()
    first = checks(range(30))
    second = checks(range(32))
    candidate = checks(range(10, 45))
    expected = score.compare_rewrites(ids, first, second, candidate)
    random.Random(1).shuffle(first)
    random.Random(2).shuffle(second)
    random.Random(3).shuffle(candidate)
    random.Random(4).shuffle(ids)
    original = copy.deepcopy((ids, first, second, candidate))
    result = score.compare_rewrites(ids, first, second, candidate)
    assert result == expected
    assert result["comparison"]["b"] == 15 and result["comparison"]["c"] == 10
    assert (ids, first, second, candidate) == original


def test_rewrite_spread_consumes_each_checker_iterator_once():
    """REWRITE-SPREAD: reuse aligned outcomes for both spread and comparison."""
    result = score.compare_rewrites(
        source_ids(), iter(checks(range(20))), iter(checks(range(21))),
        iter(checks(range(30))))
    assert result["r0_first"] == {"passed": 20, "rate": 0.2}
    assert result["r0_second"] == {"passed": 21, "rate": 0.21}
    assert result["candidate"] == {"passed": 30, "rate": 0.3}
    assert result["decode_difference"] == 0.01
    comparison = result["comparison"]
    assert (comparison["b"], comparison["c"], comparison["pairs"]) == (10, 0, 100)
    assert comparison["difference"] == 0.1 and comparison["result"] == "higher"
    spread = score.rewrite_spread(
        source_ids(), iter(checks(range(20))), iter(checks(range(21))))
    assert spread == {key: result[key] for key in spread}


@pytest.mark.parametrize("which", [0, 1, 2])
@pytest.mark.parametrize("change, message", [
    (lambda rows: rows.pop(), "results are missing"),
    (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate or unknown"),
    (lambda rows: rows[0].update(id="unknown"), "duplicate or unknown"),
    (lambda rows: rows[0]["check"].update(ok=1), "boolean checker result"),
    (lambda rows: rows[0]["check"].update(ok=None), "boolean checker result"),
    (lambda rows: rows[0].pop("check"), "boolean checker result"),
])
def test_rewrite_spread_rejects_bad_membership_in_each_run(which, change, message):
    """REWRITE-KIT, REWRITE-SPREAD: refuse subsets and absent verdicts."""
    runs = [checks(set()), checks(set()), checks(range(10))]
    change(runs[which])
    with pytest.raises(score.ScoreError, match=message):
        score.compare_rewrites(source_ids(), *runs)


@pytest.mark.parametrize("change, message", [
    (lambda ids: ids.pop(), "100 source IDs"),
    (lambda ids: ids.__setitem__(0, ids[1]), "source IDs repeat"),
    (lambda ids: ids.__setitem__(0, ""), "100 source IDs"),
    (lambda ids: ids.__setitem__(0, 0), "100 source IDs"),
])
def test_rewrite_spread_requires_the_full_source_set(change, message):
    """REWRITE-KIT: the emitter plan or an ambiguous source list cannot be used."""
    ids = source_ids()
    change(ids)
    with pytest.raises(score.ScoreError, match=message):
        score.rewrite_spread(ids, checks(set()), checks(set()))


def test_rewrite_spread_keeps_missing_outputs_in_the_denominator():
    """REWRITE-CHECK, REWRITE-SPREAD: retain failed rows for missing outputs."""
    first = checks(set())
    second = checks(set())
    for rows in (first, second):
        for row in rows:
            row.update(raw=None, output=None, scorer_included=False)
            row["check"]["hard"] = {"missing": [True]}
    result = score.compare_rewrites(source_ids(), first, second, checks(range(10)))
    assert result["denominator"] == 100 and result["comparison"]["pairs"] == 100
    assert result["r0_first"]["passed"] == 0
    assert result["candidate"]["rate"] == 0.1
    assert result["comparison"]["result"] == "higher"
