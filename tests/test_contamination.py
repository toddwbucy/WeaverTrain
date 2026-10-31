"""Positive and negative controls for scripts/contamination.py: a scan that reports no
hits is only evidence if the same matcher finds a planted copy of a task."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contamination.py"
spec = importlib.util.spec_from_file_location("contamination", SCRIPT)
contamination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contamination)

SWE_TASK = {"instance_id": "acme__widget-42", "repo": "acme/widget", "base_commit": "a" * 40,
            "problem_statement": "The widget breaks when\n the input is   empty."}
MSB_TASK = {"instance_id": "org__rr-7", "org": "org", "repo": "rr", "number": 7,
            "base": {"sha": "b" * 40}, "resolved_issues": [{"number": 5, "body": "Panics on an empty glob."}]}


def row(uuid, repo, user, extra=""):
    raw = json.dumps({"uuid": uuid, "repo": repo, "messages": [{"role": "user", "content": user}]}) + extra
    return uuid, repo, [user], raw


def test_finds_each_planted_key_and_nothing_else():
    rows = [
        row("u1", "Acme/Widget", "Please fix this: The widget breaks when the input is empty. Thanks"),
        row("u2", "other/x", "unrelated", f" checkout {'a' * 40}"),
        row("u3", None, "see https://github.com/org/rr/issues/5 for the report"),
        row("u4", "other/y", "the task acme__widget-42 again"),
        # near misses: same repo, other number, longer id, other commit, other text
        row("n1", "acme/widget", "The widget is slow.", " github.com/acme/widget/pull/420 acme__widget-421 " + "c" * 40),
    ]
    out = contamination.match([SWE_TASK, MSB_TASK], rows)
    keys = {(h["task"], h["uuid"]): h["keys"] for h in out["hits"]}
    assert keys == {("acme__widget-42", "u1"): ["issue_text"], ("acme__widget-42", "u2"): ["base_commit"],
                    ("org__rr-7", "u3"): ["github_ref"], ("acme__widget-42", "u4"): ["instance_id"]}
    assert out["drop"] == ["u1", "u2", "u3", "u4"]
    assert out["tasks_matched"] == 2 and out["examples"] == 5
    assert out["repo_overlap"] == [{"repo": "acme/widget", "examples": 2, "tasks": 1}]


def test_multi_swe_bench_issue_body_is_the_statement():
    rows = [row("u1", "org/rr", "Bug: Panics on an empty glob. Please help.")]
    out = contamination.match([MSB_TASK], rows)
    assert [(h["task"], h["keys"]) for h in out["hits"]] == [("org__rr-7", ["issue_text"])]
