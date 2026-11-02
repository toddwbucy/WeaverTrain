"""scripts/ste_gemini.py: request shape, reading a response, the cost estimate. No
network: the API calls themselves are checked by hand against a live key."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ste_gemini", SCRIPTS / "ste_gemini.py")
gem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gem)

PROMPT = {"system": "RULES", "examples": [{"role": "user", "content": "<<<TEXT\na\nTEXT>>>"},
                                          {"role": "assistant", "content": "A."}]}


def test_round_one_request_puts_the_rules_first_then_the_text():
    r = gem.request({"id": "t1", "text": "Let me look."}, PROMPT, "high")
    assert r["systemInstruction"]["parts"][0]["text"] == "RULES"
    assert [c["role"] for c in r["contents"]] == ["user", "model", "user"]
    assert r["contents"][-1]["parts"][0]["text"] == "<<<TEXT\nLet me look.\nTEXT>>>"
    assert r["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}


def test_repair_request_adds_the_last_rewrite_and_the_notes():
    rec = {"id": "t1", "text": "Let me look.", "previous": "Let me look.", "problems": "- verbs: let"}
    r = gem.request(rec, PROMPT, "high", cache="cachedContents/x")
    assert r["cachedContent"] == "cachedContents/x" and "systemInstruction" not in r
    assert [c["role"] for c in r["contents"]] == ["user", "model", "user"]
    assert "- verbs: let" in r["contents"][-1]["parts"][0]["text"]


def test_thought_parts_are_not_the_rewrite():
    resp = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": "I will look. "}]}}]}
    assert gem.text_of(resp) == ("I will look.", "STOP")
    assert gem.text_of({"promptFeedback": {"blockReason": "SAFETY"}}) == (None, "SAFETY")


def test_cost_counts_thinking_as_output_and_cached_input_cheaply():
    u = {"promptTokenCount": 1_000_000, "cachedContentTokenCount": 400_000,
         "candidatesTokenCount": 100_000, "thoughtsTokenCount": 100_000}
    assert gem.cost(u) == round(0.6 * 1.00 + 0.4 * 0.05 + 0.2 * 5.00, 4)
    assert gem.cost(u, 2.0) == round(2 * (0.6 * 1.00 + 0.4 * 0.05 + 0.2 * 5.00), 4)


def test_group_request_and_answer(monkeypatch):
    import json
    from types import SimpleNamespace
    recs = [{"id": "t1", "text": "Let me look."}, {"id": "t2", "text": "Done."}]
    assert [len(u) for u in gem.units(recs * 3, 4)] == [4, 2]
    monkeypatch.setattr(gem.exp, "render_rules",
                        lambda p: 'Each record of input.jsonl holds one text in its "text" field. RULES')
    r = gem.unit_request(recs, PROMPT, SimpleNamespace(group=20, thinking="high"), None)
    assert "<<<TEXT id=" in r["systemInstruction"]["parts"][0]["text"]
    assert r["generationConfig"]["responseMimeType"] == "application/json"
    msg = r["contents"][0]["parts"][0]["text"]
    assert "<<<TEXT id=t1\nLet me look.\nTEXT>>>" in msg and "<<<TEXT id=t2\nDone.\nTEXT>>>" in msg
    resp = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps([{"id": "t1", "output": "I will look."}, {"id": "zz", "output": "x"}])}]}}]}
    out, problems = gem.unit_outputs(resp, recs, 20)
    assert out == {"t1": "I will look."} and problems == ["t2: missing from the response"]
    rep = gem.group_message([{"id": "t1", "text": "a", "previous": "b", "problems": "- verbs: let"}])
    assert rep.startswith("These rewrites break the rules") and "- verbs: let" in rep and "<<<REWRITE\nb" in rep
    missing = gem.group_message([{"id": "t2", "text": "a", "previous": "", "problems": "- missing"}])
    assert "did not return a rewrite of t2" in missing and "<<<REWRITE" not in missing


def test_cut_off_answer_is_not_a_rewrite():
    # PR #4 review: a MAX_TOKENS answer must be asked for again, not judged as a rewrite
    recs = [{"id": "t1", "text": "Let me look at the parser and the tests."}]
    resp = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "I will look at"}]}}]}
    out, problems = gem.unit_outputs(resp, recs, 1)
    assert out == {} and problems == ["t1..: finish MAX_TOKENS, not used"]
