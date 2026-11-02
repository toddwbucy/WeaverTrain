"""Spec 0.3 section 9: frozen score sets, extraction, request validity and paired margins."""

import json
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lora_score as score
from tests.conftest import NAME, SPLIT
from tests.test_lora_build import row, tokenizer
from weaver_train import manifest as mf


TOOLS = [{"type": "function", "function": {"name": "finish", "parameters": {
    "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
         {"type": "function", "function": {"name": "read", "parameters": {
             "type": "object", "properties": {"path": {"type": "string"}, "options": {"type": "object"}},
             "required": ["path"]}}}]


def build_record(tok, length=4096):
    """RECONCILE-FIELDS: each fixture uses a complete render contract."""
    return {"tree_hash": "model-hash", "template_hash": score.build.template_hash(tok),
            "tools": True, "enable_thinking": False, "max_seq_length": length}


def kit_for(source, key="sample"):
    """SCORE-LOCATE: the kit identifies a segment without a dataset row number."""
    return {"id": key, "kind": "narration", "uuid": source["uuid"],
            "loc": {"msg": 2, "field": "content"}, "raw": source["messages"][2]["content"]}


@pytest.mark.parametrize("field", score.FIELDS)
def test_reconcile_fields_rejects_missing_and_changed_values(tokenizer, field):
    record = build_record(tokenizer)
    record.pop(field)
    with pytest.raises(score.ScoreError, match=field):
        score.validate_build(record, tokenizer, "model-hash", 4096)
    record = build_record(tokenizer)
    record[field] = "different"
    with pytest.raises(score.ScoreError, match=field):
        score.validate_build(record, tokenizer, "model-hash", 4096)


def test_score_locate_prefix_freezes_exclusions_and_request_set(tokenizer):
    sources = [row(i) for i in range(4)]
    for source in sources:
        source["messages"][2]["tool_calls"] = [{"function": {"name": "read", "arguments": '{"path":"x"}'}}]
        source["tools"] = TOOLS
    sources[2]["messages"][1]["content"] = "issue " * 5000
    kit = [kit_for(source, f"s{i}") for i, source in enumerate(sources)]
    kit.append({**kit_for(sources[0], "no-match"), "raw": "not the original"})
    rows = [sources[0], sources[1], sources[1], sources[2]]
    plan = score.make_plan(tokenizer, kit, rows, build_record(tokenizer))
    assert [entry["id"] for entry in plan["entries"]] == ["s0"]
    assert plan["request_set"] == plan["review_sample"] == ["s0"]
    reasons = {item["id"]: item for item in plan["excluded"]}
    assert reasons["s1"]["matches"] == 2
    assert reasons["s3"]["matches"] == reasons["no-match"]["matches"] == 0
    assert reasons["s2"]["cause"] == "SCORE-PREFIX"
    entry = plan["entries"][0]
    assert entry["prompt"]["messages"] == sources[0]["messages"][:2]
    assert entry["prompt"]["tools"] == TOOLS
    assert entry["input_ids"] == score.build.render_row(tokenizer, entry["prompt"],
                                                        enable_thinking=False, add_generation_prompt=True)
    assert plan == score.make_plan(tokenizer, kit, rows, build_record(tokenizer))
    with pytest.raises(score.ScoreError, match="unique"):
        score.make_plan(tokenizer, kit + [kit[0]], rows, build_record(tokenizer))


def native_call(name="finish", parameters="<parameter=message>\nThe work is complete.\n</parameter>"):
    """SCORE-EXTRACT: Nemotron's native template surrounds requests with these tags."""
    return f"<tool_call>\n<function={name}>\n{parameters}\n</function>\n</tool_call>"


@pytest.mark.parametrize("output, valid", [
    (native_call(), True),
    ('<tool_call>{"name":"finish","arguments":{"message":"Done."}}</tool_call>', True),
    ('<tool_call>{"name":"finish","arguments":"{\\"message\\":\\"Done.\\"}"}</tool_call>', True),
    ('<tool_call>{"name":"missing","arguments":{"message":"Done."}}</tool_call>', False),
    ('<tool_call>{"name":"finish","arguments":{}}</tool_call>', False),
    ('<tool_call>{"name":"finish","arguments":{"message":NaN}}</tool_call>', False),
    ('<tool_call>{"name":"finish","arguments":[]}</tool_call>', False),
    ('<tool_call>{broken}</tool_call>', False),
    (native_call() + '<tool_call>', False),
    (native_call() + native_call("missing"), False),
    (native_call("read", '<parameter=path>x</parameter><parameter=options>{broken}</parameter>'), False),
    (native_call("read", '<parameter=path>x</parameter><parameter=options>{"line":1}</parameter>'), True),
    ("The work is complete.", False),
])
def test_score_validity_requires_every_request(output, valid):
    assert score.valid_requests(score.parse_requests(output, TOOLS), TOOLS) is valid


def test_score_extract_and_check_masks_code_without_rewrite_comparison():
    """SCORE-EXTRACT and SCORE-CHECK: judge the output itself and retain missing-text failures."""
    entry = {"id": "s1", "kind": "narration", "prompt": {"tools": TOOLS}, "input_ids": [1], "request": True}
    seen = []

    def judge(output, reference):
        seen.append((output, reference))
        return {"ok": True, "hard": {}, "soft": {}}

    result = score.evaluate_output(entry, "Read `file.py`.\n" + native_call(), judge)
    assert result["extracted_text"] == "Read `file.py`."
    assert seen == [("Read [[C1]].", "Read [[C1]].")]
    assert result["correct_request"] is True and result["word_count"] == 2
    final = score.evaluate_output({**entry, "kind": "final"}, native_call(), judge)
    assert final["extracted_text"] == "The work is complete."
    missing = score.evaluate_output({**entry, "kind": "final"}, "The work is complete.", judge)
    assert missing["check"]["hard"]["no_text"] and not missing["correct_request"]
    assert not score.evaluate_output(entry, native_call(), judge)["check"]["ok"]


def test_score_check_refuses_altered_prompt(tmp_path, monkeypatch):
    """SCORE-CHECK: a trusted-looking metadata field cannot hide changed prompt content."""
    parent = tmp_path / "var/ste"
    parent.mkdir(parents=True)
    (parent / score.CHECKER_PROMPT).write_text(json.dumps({"system": "changed", "examples": [],
                                                         "meta": {"prompt_sha256": score.CHECKER_SHA256}}))
    monkeypatch.setattr(score, "REPO", tmp_path)
    with pytest.raises(score.ScoreError, match="prompt hash differs"):
        score.load_checker()


def test_run_margin_vectors_and_decode_spread():
    assert score.compare_pairs([False] * 100, [True] * 10 + [False] * 90, 0)["result"] == "higher"
    assert score.compare_pairs([True] * 10 + [False] * 90, [False] * 100, 0)["result"] == "lower"
    five = score.compare_pairs([False] * 100, [True] * 5 + [False] * 95, 0)
    assert five["p"] == 0.0625 and five["result"] == "not shown"
    suppressed = score.compare_pairs([False] * 100, [True] * 10 + [False] * 90, 0.11)
    assert suppressed["p"] < 0.05 and suppressed["result"] == "not shown"
    flat = score.compare_pairs([True] * 100, [True] * 100, 0)
    assert flat["result"] == "flat"
    assert -0.05 <= flat["interval_95"][0] <= flat["interval_95"][1] <= 0.05
    assert "100 segments" in flat["limit"]
    # Independent published implementation: statsmodels method 10, paired table [[10, 5], [2, 20]].
    a = [True] * 10 + [False] * 5 + [True] * 2 + [False] * 20
    b = [True] * 15 + [False] * 22
    assert score.paired_interval(a, b) == pytest.approx((-0.0643926050711, 0.2211160178958))


def test_score_review_freezes_sample_blinds_procedures_and_marks_operator(tmp_path, tokenizer):
    sources = [row(i) for i in range(35)]
    plan = score.make_plan(tokenizer, [kit_for(source, f"s{i}") for i, source in enumerate(sources)],
                           sources, build_record(tokenizer))
    assert len(plan["review_sample"]) == 30
    prompt = tmp_path / "review.md"
    prompt.write_text("Assess the work and the next action.\n")
    runs = {name: [{"id": entry["id"], "output": "Read the file."} for entry in plan["entries"]]
            for name in ("baseline", "epoch-1")}
    bundle, mapping = score.review_bundle(plan, runs, prompt)
    assert bundle["model"] == "claude-opus-5-5"
    assert bundle["prompt_sha256"] == mf.sha256_file(prompt)
    assert len(bundle["items"]) == 60 and bundle["count_per_run"] == 30
    assert all(set(item) == {"id", "prompt", "output"} for item in bundle["items"])
    assert "baseline" not in json.dumps(bundle) and "epoch-1" not in json.dumps(bundle)
    for name in runs:
        assert {item["segment"] for item in mapping.values() if item["run"] == name} == set(plan["review_sample"])
    verdicts = [{"id": item["id"], "decision": "fits"} for item in bundle["items"]]
    reviewed = score.apply_review(bundle, mapping, verdicts, [{"id": verdicts[0]["id"], "decision": "odd"}])
    assert reviewed["decisions"][0]["operator_override"] is True
    assert reviewed["decisions"][0]["model_decision"] == "fits"
    with pytest.raises(score.ScoreError, match="exactly once"):
        score.apply_review(bundle, mapping, verdicts[:-1])


def write_scored(directory, plan_path, outcomes):
    """SCORE-SPREAD: independent fixture records keep request membership explicit."""
    directory.mkdir()
    plan = json.loads(plan_path.read_text())
    record = {"status": "completed", "engine": "python-hf", "plan_sha256": mf.sha256_file(plan_path),
              "adapter_sha256": "none", "request_set": plan["request_set"],
              **{field: plan[field] for field in score.FIELDS}}
    (directory / "score.json").write_text(json.dumps(record))
    rows = [{"id": entry["id"], "check": {"ok": ok}, "correct_request": ok if entry["request"] else None}
            for entry, ok in zip(plan["entries"], outcomes)]
    (directory / "segments.jsonl").write_text("".join(json.dumps(item) + "\n" for item in rows))


def test_score_spread_compares_same_request_set_and_rejects_missing_result(tmp_path, tokenizer):
    sources = [row(i) for i in range(100)]
    kit = [{"id": f"s{i}", "kind": "final", "uuid": source["uuid"],
            "loc": {"msg": 4, "field": "finish", "call": 0}, "raw": "Done"}
           for i, source in enumerate(sources)]
    plan = score.make_plan(tokenizer, kit, sources, build_record(tokenizer))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    paths = [tmp_path / name for name in ("a", "b", "r0-first", "r0-second")]
    for path, n in zip(paths, (0, 10, 0, 11)):
        write_scored(path, plan_path, [True] * n + [False] * (100 - n))
    comparisons = score.compare_scores(plan_path, *paths)
    assert comparisons["ste"]["decode_difference"] == 0.11
    assert comparisons["correct_request"]["result"] == "not shown"
    bad = paths[1] / "segments.jsonl"
    rows = score.build.read_jsonl(bad)
    rows[0]["correct_request"] = None
    bad.write_text("".join(json.dumps(item) + "\n" for item in rows))
    with pytest.raises(score.ScoreError, match="boolean result"):
        score.compare_scores(plan_path, *paths)


@pytest.mark.multigpu
def test_score_generate_twice_on_split_small_model(tmp_path, tiny_model, monkeypatch):
    """SCORE-GENERATE, SCORE-SPREAD, SCORE-OUTPUT: actual greedy generation on both GPUs."""
    from transformers import AutoTokenizer

    model_dir, manifest_path = tiny_model
    tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    tok.add_special_tokens({"additional_special_tokens": ["<|im_end|>"]})
    tok.chat_template = "{% for m in messages %}{{ m.content }} {% endfor %}{% if add_generation_prompt %}the {% endif %}"
    tok.save_pretrained(model_dir)
    manifest = mf.build_tree(model_dir, "model", NAME, local_dir_name=model_dir.name)
    manifest.save(manifest_path)
    monkeypatch.setenv("WEAVER_TRAIN_MANIFEST_DIR", str(manifest_path.parent))
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_DIR", str(model_dir))
    original_load = score.loader.load
    monkeypatch.setattr(score.loader, "load", lambda name, **kwargs: original_load(name, device_map=SPLIT))
    monkeypatch.setattr(score, "OUTPUT_LIMIT", 4)
    checker_dir = tmp_path / "var/ste"
    checker_dir.mkdir(parents=True)
    checker_hash = hashlib.sha256(json.dumps(["fixture rules", []]).encode()).hexdigest()
    checker_record = {"meta": {"prompt_sha256": checker_hash, "limit_instruction": 20, "limit_description": 25},
                      "system": "fixture rules", "examples": []}
    (checker_dir / score.CHECKER_PROMPT).write_text(json.dumps(checker_record))
    monkeypatch.setattr(score, "REPO", tmp_path)
    monkeypatch.setattr(score, "CHECKER_SHA256", checker_hash)
    monkeypatch.setattr(score.ste_experiment, "REPO", tmp_path)
    record = {**build_record(tok), "tree_hash": manifest.tree_hash}
    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps(record))
    source = row()
    source["messages"][2]["content"] = "the model"
    plan = score.make_plan(tok, [kit_for(source)], [source], record)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    prompt = tmp_path / "review.md"
    prompt.write_text("Assess the work.\n")
    results = []
    for index in range(2):
        dest = tmp_path / f"r0-{index}"
        measured = score.score_model(plan_path, build_path, dest, model_name=NAME, prompt_path=prompt)
        assert measured["status"] == "completed" and measured["review_status"] == "pending"
        assert measured["sampler"]["do_sample"] is False
        assert measured["reviewer_model"] == "claude-opus-5-5"
        assert measured["reviewer_prompt_sha256"] == mf.sha256_file(prompt)
        assert measured["review_sample"] == ["sample"] and measured["review_count"] == 1
        assert all(peak > 0 for peak in measured["peak_vram_gib"].values())
        results.append(score.read_score(dest, plan_path)[1])
    assert results[0] == results[1]
