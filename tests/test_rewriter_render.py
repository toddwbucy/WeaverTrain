"""Spec 0.2 section 9: folded messages, final-target masks and GPU loss labels."""

import copy
import hashlib
import json
import string
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_build as build
from tests.test_trainer import CONFIG
from weaver_train import manifest as mf
from weaver_train import trainer

# RENDER-LAYOUT: synthetic fixture text only, with four examples inside the system.
FOLDED = "rules\n" + "example input a output b\n" * 4


@pytest.fixture
def tokenizer():
    """RENDER-MASK: the fixture tokenizer retains a separate token for the final LF."""
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    tokens = ["<unk>", "<|im_start|>", "<|im_end|>"]
    tokens += list(string.ascii_letters + " <>\n.")
    raw = Tokenizer(models.WordLevel({v: i for i, v in enumerate(tokens)},
                                     unk_token="<unk>"))
    raw.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")
    tok = PreTrainedTokenizerFast(tokenizer_object=raw, unk_token="<unk>",
                                  additional_special_tokens=tokens[1:3])
    tok.chat_template = (
        "{% for m in messages %}"
        "{{ '<|im_start|>' + m.role + '\n' + m.content + '<|im_end|>\n' }}"
        "{% endfor %}"
        "{% if add_generation_prompt %}{{ '<|im_start|>assistant\n' }}{% endif %}"
    )
    return tok


@pytest.fixture
def pair():
    """RENDER-OUTPUT: synthetic pair identity from the PAIR-OUTPUT shape."""
    return {"batch": "200", "id": "T001.001", "trajectory": "T001", "uuid": "task",
            "row": 9, "kind": "narration", "loc": {"msg": 0, "field": "content"},
            "target_round": 2, "input": "input", "target": "output"}


def test_render_seed_preserves_bytes_and_final_lf():
    """RENDER-SEED: UTF-8 decoding preserves both source characters and whitespace."""
    raw = (" " + FOLDED + "\u00b0\r\n").encode("utf-8")
    result = build.folded_system(raw, hashlib.sha256(raw).hexdigest())
    assert result.encode("utf-8") == raw


@pytest.mark.parametrize("folded", [None, "rules", b"", FOLDED.rstrip().encode(),
                                     FOLDED.replace("\n", "\r\n").encode()])
def test_render_seed_rejects_absent_or_changed_file_bytes(folded):
    """RENDER-SEED: the local file must match its pin without a seed record."""
    raw = FOLDED.encode()
    with pytest.raises(build.RenderError, match="RENDER-SEED"):
        build.folded_system(folded, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("source", [b"grouped instruction", b"single bundle", b"system"])
def test_render_seed_rejects_source_provenance_in_place_of_folded_pin(source):
    """RENDER-SEED: none of the three provenance identities is the folded identity."""
    raw = FOLDED.encode()
    with pytest.raises(build.RenderError, match="folded prompt sha256 differs"):
        build.folded_system(raw, hashlib.sha256(source).hexdigest())


def test_render_seed_rejects_non_utf8_bytes():
    """RENDER-SEED: invalid text bytes stop before message construction."""
    raw = bytes([255])
    with pytest.raises(build.RenderError, match="not UTF-8"):
        build.folded_system(raw, hashlib.sha256(raw).hexdigest())


def test_render_layout_messages_and_template_options(tokenizer, pair, monkeypatch):
    """RENDER-LAYOUT: one folded system, one user, no tools and thinking false."""
    pair["input"] = " \ninput\n "
    pair["target"] = " output\n"
    original = copy.deepcopy(pair)
    apply = tokenizer.apply_chat_template
    calls = []

    def observe(messages, **kwargs):
        """RENDER-LAYOUT: inspect the complete template invocation."""
        calls.append((copy.deepcopy(messages), kwargs.copy()))
        return apply(messages, **kwargs)

    monkeypatch.setattr(tokenizer, "apply_chat_template", observe)
    build.render_pair(tokenizer, pair, FOLDED)
    initial = [{"role": "system", "content": FOLDED},
               {"role": "user", "content": "<<<TEXT\n \ninput\n \nTEXT>>>"}]
    assert calls == [
        (initial, {"enable_thinking": False, "tokenize": True,
                   "return_dict": False, "add_generation_prompt": True}),
        (initial + [{"role": "assistant", "content": " output\n"}],
         {"enable_thinking": False, "tokenize": True,
          "return_dict": False, "add_generation_prompt": False}),
    ]
    assert pair == original


def test_render_mask_exact_target_end_token_and_lf(tokenizer, pair):
    """RENDER-MASK: system examples, input and assistant header receive no loss."""
    row = build.render_pair(tokenizer, pair, FOLDED)
    prefix = ("<|im_start|>system\n" + FOLDED + "<|im_end|>\n"
              "<|im_start|>user\n<<<TEXT\ninput\nTEXT>>><|im_end|>\n"
              "<|im_start|>assistant\n")
    prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
    tail_ids = tokenizer.encode("output<|im_end|>\n", add_special_tokens=False)
    assert row["input_ids"] == prefix_ids + tail_ids
    assert row["assistant_masks"] == [0] * len(prefix_ids) + [1] * len(tail_ids)
    assert tail_ids[-2:] == [tokenizer.convert_tokens_to_ids("<|im_end|>"),
                             tokenizer.convert_tokens_to_ids("\n")]


def test_render_output_identity_and_canonical_initial_list(tokenizer, pair):
    """RENDER-OUTPUT, RENDER-SEED: retain identity and hash exact canonical JSON."""
    pair["input"] = "\u00b0"
    row = build.render_pair(tokenizer, pair, "system\n")
    expected = (b'[{"role":"system","content":"system\\n"},'
                b'{"role":"user","content":"<<<TEXT\\n\\u00b0\\nTEXT>>>"}]')
    assert row["initial_messages_sha256"] == hashlib.sha256(expected).hexdigest()
    assert {key: row[key] for key in pair if key not in ("input", "target")} == {
        key: value for key, value in pair.items() if key not in ("input", "target")}
    assert not {"input", "target", "prompt_provenance"} & row.keys()


def test_render_mask_rejects_changed_prefix(tokenizer, pair):
    """RENDER-MASK: a template that changes earlier tokens cannot yield a row."""
    tokenizer.chat_template = (
        "{% if messages | length == 3 %}changed{% endif %}" + tokenizer.chat_template)
    with pytest.raises(build.RenderError, match="RENDER-MASK.*200/T001.001"):
        build.render_pair(tokenizer, pair, FOLDED)


def test_render_mask_rejects_no_added_span(pair):
    """RENDER-MASK: a template that drops the target cannot yield an all-zero mask."""
    tok = SimpleNamespace(apply_chat_template=lambda *args, **kwargs: [1, 2])
    with pytest.raises(build.RenderError, match="RENDER-MASK"):
        build.render_pair(tok, pair, FOLDED)


@pytest.mark.parametrize("length", [49152, 49153])
def test_render_length_includes_prompt_and_complete_target(pair, length):
    """RENDER-LENGTH: keep the boundary sequence intact and reject one extra token."""
    def apply(messages, **kwargs):
        """RENDER-LENGTH: synthetic token counts with one valid target prefix."""
        return [1] * (length - 2 if kwargs["add_generation_prompt"] else length)

    tok = SimpleNamespace(apply_chat_template=apply)
    if length == 49152:
        row = build.render_pair(tok, pair, FOLDED)
        assert len(row["input_ids"]) == length
        assert sum(row["assistant_masks"]) == 2
    else:
        with pytest.raises(build.RenderError, match="RENDER-LENGTH.*49153.*49152"):
            build.render_pair(tok, pair, FOLDED)


@pytest.mark.gpu
def test_rewrite_train_folded_labels_at_loss_and_epoch_adapters(
        tiny_model, tmp_path, monkeypatch, tokenizer, pair):
    """REWRITE-TRAIN: inspect final-target labels at the real GPU model's forward."""
    model_dir, model_manifest = tiny_model
    rows = [build.render_pair(tokenizer, {**pair, "target": target}, FOLDED)
            for target in ("output", "output a")]
    data = tmp_path / "synthetic-data"
    data.mkdir()
    dataset = data / "train.jsonl"
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    model_hash = mf.load_tree(model_manifest).tree_hash
    # REWRITE-TRAIN: fixture-only record for the small model, never a real seed claim.
    (data / "build.json").write_text(json.dumps({
        "tree_hash": model_hash, "max_seq_length": 1024,
    }), encoding="utf-8")
    mf.build_tree(data, "dataset", "synthetic-rewriter").save(
        data.with_name(data.name + ".manifest.json"))
    config = tmp_path / "train.toml"
    config.write_text(CONFIG.replace('format = "text"', 'format = "tokenized"')
                      .replace("max_steps = 2", "num_train_epochs = 2")
                      .replace("max_seq_length = 64", "max_seq_length = 1024"),
                      encoding="utf-8")
    checked = trainer.checked_trainer_class
    observed = []

    def inspected(base):
        """REWRITE-TRAIN: inspect after the trainer and adapter have processed inputs."""
        class Inspected(checked(base)):
            """REWRITE-TRAIN: preserve all trainer stop checks during inspection."""
            def compute_loss(self, model, inputs, *args, **kwargs):
                """REWRITE-TRAIN: observe labels at the underlying model boundary."""
                def at_forward(module, positional, keywords):
                    """REWRITE-TRAIN: copy the labels that reach the real loss."""
                    observed.append((keywords["input_ids"].detach().cpu().tolist()[0],
                                     keywords["labels"].detach().cpu().tolist()[0]))
                handle = model.get_base_model().register_forward_pre_hook(
                    at_forward, with_kwargs=True)
                try:
                    return super().compute_loss(model, inputs, *args, **kwargs)
                finally:
                    handle.remove()
        return Inspected

    monkeypatch.setattr(trainer, "checked_trainer_class", inspected)
    out = tmp_path / "synthetic-run"
    record = trainer.train(model_dir, model_manifest, dataset, config, out)
    expected = {}
    for row, target in zip(rows, ("output", "output a")):
        tail = tokenizer.encode(target + "<|im_end|>\n", add_special_tokens=False)
        expected[tuple(row["input_ids"])] = [-100] * (len(row["input_ids"]) - len(tail)) + tail
    assert len(observed) == 4
    assert {tuple(ids) for ids, _ in observed} == expected.keys()
    for ids, labels in observed:
        assert labels == expected[tuple(ids)]
    assert record.status == "completed" and record.results["epochs"] == 2
    assert record.results["global_step"] == 4
    for name in ("adapter-epoch-1", "adapter-epoch-2", "adapter"):
        trainer.check_adapter_only(out / name)
        assert mf.verify_tree(mf.load_tree(out / (name + ".manifest.json")),
                              out / name) == []
