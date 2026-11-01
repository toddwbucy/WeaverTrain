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
