"""Spec 0.3 section 9: input integrity, disjoint tasks, masks and reproducible subsets."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lora_build as build
from weaver_train import manifest as mf


@pytest.fixture
def tokenizer():
    """Small tokenizer with visible role and tool boundaries for BUILD-MASK."""
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    words = ["<unk>", "system", "user", "assistant", "tool", "end", "sys", "issue",
             "answer", "output", "finish", "Done", "tools", "name", "message"]
    raw = Tokenizer(models.WordLevel({word: i for i, word in enumerate(words)}, unk_token="<unk>"))
    raw.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tok = PreTrainedTokenizerFast(tokenizer_object=raw, unk_token="<unk>")
    tok.chat_template = (
        "{% if tools %}tools {% endif %}"
        "{% for m in messages %}{{ m.role }} {{ m.content | default('', true) }} "
        "{% for c in m.tool_calls | default([]) %}{{ c.function.name }} "
        "{{ c.function.arguments.message }} {% endfor %}end {% endfor %}"
        "{% if add_generation_prompt %}assistant {% endif %}"
    )
    return tok


def row(number=0):
    """Fixture provenance and tool arguments follow BUILD-RENDER's input shape."""
    return {"uuid": f"task-{number}",
            "ste": {"batch": "one", "trajectory": f"T{number}", "row": number},
            "tools": [{"type": "function", "function": {"name": "finish"}}],
            "messages": [{"role": "system", "content": "sys"},
                         {"role": "user", "content": "issue"},
                         {"role": "assistant", "content": "answer"},
                         {"role": "tool", "content": "output"},
                         {"role": "assistant", "content": "", "tool_calls": [
                             {"function": {"name": "finish", "arguments": '{"message":"Done"}'}}]}]}


def inputs(tmp_path, rows, name="one"):
    """BUILD-INPUTS: source digest comes from the neighboring batch run record."""
    parent = tmp_path / name
    parent.mkdir()
    path = parent / "converted.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))
    (parent / "run.json").write_text(json.dumps({"batch": name, "converted_sha256": mf.sha256_file(path)}))
    return path


def heldout(tmp_path, uuids=()):
    path = tmp_path / "heldout.jsonl"
    path.write_text("".join(json.dumps({"uuid": uuid}) + "\n" for uuid in uuids))
    return path


def test_build_inputs_rejects_bad_digest(tmp_path):
    path = inputs(tmp_path, [row()])
    path.write_text(path.read_text() + "\n")
    with pytest.raises(build.BuildError, match="BUILD-INPUTS.*sha256"):
        build.load_inputs([path], heldout(tmp_path))


def test_build_inputs_rejects_repeated_dataset_row(tmp_path):
    paths = [inputs(tmp_path, [row()], name) for name in ("one", "two")]
    with pytest.raises(build.BuildError, match="duplicate dataset row 0"):
        build.load_inputs(paths, heldout(tmp_path))


def test_build_heldout_uses_uuid_not_row(tmp_path):
    source = row(999)
    source["uuid"] = "held-out-task"
    path = inputs(tmp_path, [source])
    with pytest.raises(build.BuildError, match="BUILD-HELDOUT.*999"):
        build.load_inputs([path], heldout(tmp_path, ["held-out-task"]))


def test_build_mask_covers_assistant_tool_arguments_and_end_only(tokenizer):
    """BUILD-MASK: headers and all non-assistant tokens remain outside loss."""
    source = row()
    before = copy.deepcopy(source)
    result, ste_count = build.tokenize_row(tokenizer, source, enable_thinking=False)
    tokens = tokenizer.convert_ids_to_tokens(result["input_ids"])
    supervised = [token for token, mask in zip(tokens, result["assistant_masks"]) if mask]
    assert supervised == ["answer", "end", "finish", "Done", "end"]
    assert len(result["input_ids"]) == len(result["assistant_masks"])
    assert ste_count == 2
    assert source == before


def test_build_prefix_stops_with_row_and_message(tokenizer):
    """BUILD-PREFIX: a history-dependent template is refused, never repaired."""
    tokenizer.chat_template = (
        "{% if messages | length > 2 %}answer {% else %}issue {% endif %}"
        + tokenizer.chat_template
    )
    with pytest.raises(build.BuildError, match="BUILD-PREFIX: row 0, message 2"):
        build.tokenize_row(tokenizer, row(), enable_thinking=False)


def test_build_prefix_checks_full_trajectory(tokenizer):
    """BUILD-PREFIX: a locally stable assistant span must also prefix the complete row."""
    tokenizer.chat_template = (
        "{% if messages | length > 4 %}answer {% else %}issue {% endif %}"
        + tokenizer.chat_template
    )
    with pytest.raises(build.BuildError, match="assistant versus full row"):
        build.tokenize_row(tokenizer, row(), enable_thinking=False)


def test_build_length_order_output_and_reconcile_fields(tmp_path, tokenizer):
    rows = [row(i) for i in range(5)]
    rows[-1]["messages"][1]["content"] = "issue " * 100
    path = inputs(tmp_path, rows)
    kit = heldout(tmp_path)
    results = []
    for limit in (1, 2, None):
        dest = tmp_path / f"build-{limit}"
        record = build.build_dataset(tokenizer, [path], kit, dest, tree_hash="model-hash",
                                     max_seq_length=50, seed=0, limit=limit, enable_thinking=False)
        results.append(build.read_jsonl(dest / "train.jsonl"))
        assert record["counts"]["rows_in"] == 5
        assert record["counts"]["rows_removed"] == 1
        assert record["removed"][0]["row"] == 4
        assert record["removed"][0]["tokens"] > 50
        assert record["counts"]["rows_out"] == len(results[-1])
        assert record["counts"]["assistant_ste_tokens"] == 2 * len(results[-1])
        assert set(("tree_hash", "template_hash", "tools", "enable_thinking", "max_seq_length")) <= record.keys()
        assert record["tools"] is True and record["enable_thinking"] is False
        manifest = mf.load_tree(dest.with_name(dest.name + ".manifest.json"))
        assert not mf.verify_tree(manifest, dest)
    assert results[0] == results[1][:1] == results[2][:1]
    assert results[1] == results[2][:2]


def test_build_render_only_keeps_two_sequences_without_train_data(tmp_path, tokenizer):
    """BUILD-RENDER-ONLY and RECONCILE-FIELDS: no training dataset is emitted."""
    prompts = {"trajectory": row(), "loop": {"messages": row()["messages"][:2]}}
    dest = tmp_path / "render-only"
    record = build.build_render_only(tokenizer, prompts, dest, tree_hash="small-model-hash",
                                     max_seq_length=50, enable_thinking=False)
    assert record["render_only"] is True
    assert not (dest / "train.jsonl").exists()
    sequences = json.loads((dest / "render.json").read_text())
    assert set(sequences) == {"trajectory", "loop"}
    for name in prompts:
        assert sequences[name] == build.render_row(tokenizer, prompts[name], enable_thinking=False,
                                                   add_generation_prompt=True)
    assert not mf.verify_tree(mf.load_tree(dest.with_name(dest.name + ".manifest.json")), dest)
