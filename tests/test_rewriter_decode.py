"""Spec 0.1 section 9: shared first-rewrite decoding and stop accounting."""

import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_score as score
from tests.conftest import SPLIT


class Tokenizer:
    """REWRITE-DECODE: expose the complete prompt and raw text decode boundary."""

    unk_token_id = 1
    pad_token_id = 0
    end_token_id = 7

    def __init__(self):
        """REWRITE-DECODE: retain calls and optional last-prompt overflow."""
        self.prompts = []
        self.decodes = []
        self.overflow = False

    def apply_chat_template(self, messages, **kwargs):
        """RENDER-LAYOUT: preserve a distinct prompt for each source ID."""
        self.prompts.append((copy.deepcopy(messages), kwargs.copy()))
        if self.overflow and len(self.prompts) == 100:
            return [2] * 49152
        return [2, 3, len(self.prompts) + 8]

    def convert_tokens_to_ids(self, token):
        """REWRITE-DECODE: require the template's end-of-turn token."""
        assert token == "<|im_end|>"
        return self.end_token_id

    def decode(self, tokens, **kwargs):
        """REWRITE-CHECK: expose whitespace and special-token cleanup changes."""
        self.decodes.append((tokens.copy(), kwargs.copy()))
        return " \nKeep [[C1]] . <special>\n " if tokens else ""


class Model:
    """REWRITE-DECODE: CPU test double for generation, with declared CUDA placement."""

    def __init__(self, tokenizer):
        """REWRITE-DECODE: capture calls and supply controlled completion tokens."""
        self.tokenizer = tokenizer
        self.calls = []
        self.tail = lambda budget: [4, 7]
        self.fail_at = None
        self.corrupt_prefix = False
        self.multiple = False
        self.evaluating = False
        self.off_gpu = False

    def parameters(self):
        """REWRITE-LIMITS: expose an off-GPU case without a real GPU allocation."""
        import torch

        yield SimpleNamespace(device=torch.device("cpu" if self.off_gpu else "cuda:0"))

    def named_modules(self):
        """REWRITE-LIMITS: the unit double has no decoder layers to pin."""
        return iter(())

    def get_input_embeddings(self):
        """REWRITE-DECODE: unit tensors stay on CPU, real placement has a GPU test."""
        import torch

        return SimpleNamespace(weight=torch.empty(0))

    def eval(self):
        """REWRITE-DECODE: observe selection of inference behavior."""
        self.evaluating = True

    def generate(self, *, input_ids, attention_mask, generation_config):
        """REWRITE-DECODE: record one call and fail without an alternative output."""
        import torch

        assert self.evaluating and torch.is_inference_mode_enabled()
        assert len(self.tokenizer.prompts) == 100
        self.calls.append((input_ids.tolist(), attention_mask.tolist(), generation_config))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("fixture generation failure")
        tail = self.tail(generation_config.max_new_tokens)
        result = torch.cat((input_ids, torch.tensor([tail], dtype=torch.long)), dim=1)
        if self.corrupt_prefix:
            result[0, 0] += 1
        return result.repeat(2, 1) if self.multiple else result


@pytest.fixture
def world(tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-DECODE: freeze 100 fixture rows and a fixture prompt."""
    import torch

    folded = b"the model\n"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/rewriter-inputs.toml").write_text(
        f'folded_prompt = "{score.build.digest(folded)}"\n', encoding="utf-8")
    kit = {"rows": [{"id": f"s{n:03d}", "kind": "narration", "text": "a" * n}
                    for n in range(100)]}
    tokenizer = Tokenizer()
    model = Model(tokenizer)
    syncs = []
    monkeypatch.setattr(torch.cuda, "synchronize", lambda device: syncs.append(device))
    return SimpleNamespace(repo=tmp_path, kit=kit, folded=folded, syncs=syncs,
                           loaded=SimpleNamespace(model=model, tokenizer=tokenizer))


def test_rewrite_decode_all_inputs_once_with_raw_tokens_and_greedy_config(world):
    """REWRITE-KIT, REWRITE-DECODE: all 100 inputs share exact greedy settings."""
    original = copy.deepcopy(world.kit)
    rows = list(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert [row["id"] for row in rows] == [row["id"] for row in world.kit["rows"]]
    assert world.kit == original
    assert len(world.loaded.model.calls) == 100 and len(world.syncs) == 200
    for n, (row, call) in enumerate(zip(rows, world.loaded.model.calls)):
        ids, mask, config = call
        assert ids == [[2, 3, n + 9]] and mask == [[1, 1, 1]]
        assert config.do_sample is False and config.num_beams == 1
        assert config.num_return_sequences == 1 and config.return_dict_in_generate is False
        assert config.max_new_tokens == n // 2 + 256
        assert config.eos_token_id == 7 and config.pad_token_id == 0
        assert row["token_ids"] == [4, 7] and row["finish_reason"] == "stop"
        assert row["output"] == " \nKeep [[C1]] . <special>\n "
        assert math.isfinite(row["wall_seconds"]) and row["wall_seconds"] >= 0
    assert world.loaded.tokenizer.decodes == [([4], {
        "skip_special_tokens": False, "clean_up_tokenization_spaces": False})] * 100


@pytest.mark.parametrize("ending", ["length", "boundary_stop", "empty_stop"])
def test_rewrite_decode_finish_reason_at_budget_and_empty_stop(world, ending):
    """REWRITE-CHECK: an EOS at the budget is a stop, only an absent EOS is length."""
    tails = {"length": lambda budget: [4] * budget,
             "boundary_stop": lambda budget: [4] * (budget - 1) + [7],
             "empty_stop": lambda budget: [7]}
    world.loaded.model.tail = tails[ending]
    world.loaded.tokenizer.pad_token_id = None
    row = next(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert row["token_ids"] == tails[ending](256)
    assert row["finish_reason"] == ("length" if ending == "length" else "stop")
    assert world.loaded.model.calls[0][2].pad_token_id == 7
    assert world.loaded.tokenizer.decodes[0][0] == (
        row["token_ids"] if ending == "length" else row["token_ids"][:-1])
    retained, scorer_rows = score.prepare_outputs(world.kit, [row])
    assert retained[0]["raw"] == row and len(retained) == 100
    assert bool(scorer_rows) is (ending == "boundary_stop")
    if ending == "empty_stop":
        assert row["output"] == "" and scorer_rows == []


@pytest.mark.parametrize("end", [None, -1, True, 1])
def test_rewrite_decode_refuses_missing_end_token_before_generation(world, end):
    """REWRITE-DECODE: a missing end-of-turn token cannot silently select another."""
    world.loaded.tokenizer.end_token_id = end
    with pytest.raises(score.ScoreError, match="end-of-turn"):
        next(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert not world.loaded.model.calls


def test_rewrite_decode_last_prompt_overflow_precedes_first_generation(world):
    """REWRITE-DECODE: the last prompt's fit check precedes the first model call."""
    world.loaded.tokenizer.overflow = True
    with pytest.raises(score.ScoreError, match="s099.*exceeds"):
        next(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert len(world.loaded.tokenizer.prompts) == 100
    assert not world.loaded.model.calls


def test_rewrite_decode_refuses_off_gpu_model(world):
    """REWRITE-LIMITS: CPU offload cannot become an alternate inference path."""
    world.loaded.model.off_gpu = True
    with pytest.raises(score.ScoreError, match="parameter off GPU"):
        next(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert not world.loaded.model.calls


@pytest.mark.parametrize("tail, message", [
    (lambda budget: [], "token count"),
    (lambda budget: [4] * (budget + 1), "token count"),
    (lambda budget: [4], "no valid stop"),
    (lambda budget: [7, 4, 7], "no valid stop"),
])
def test_rewrite_decode_refuses_invalid_completion_without_retry(world, tail, message):
    """REWRITE-LIMITS, REWRITE-DECODE: malformed results stop without a retry."""
    world.loaded.model.tail = tail
    with pytest.raises(score.ScoreError, match=message):
        list(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert len(world.loaded.model.calls) == 1


@pytest.mark.parametrize("mutation", ["corrupt_prefix", "multiple"])
def test_rewrite_decode_refuses_changed_prompt_or_multiple_outputs(world, mutation):
    """REWRITE-DECODE: preserve the prompt and accept only one first completion."""
    setattr(world.loaded.model, mutation, True)
    with pytest.raises(score.ScoreError, match="generated sequence"):
        next(score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo))
    assert len(world.loaded.model.calls) == 1


def test_rewrite_decode_failure_keeps_completed_rows_and_stops(world):
    """REWRITE-RECORD, REWRITE-LIMITS: yield completed IDs before a later failure."""
    world.loaded.model.fail_at = 2
    iterator = score.decode_outputs(world.loaded, world.kit, world.folded, repo=world.repo)
    first = next(iterator)
    assert first["id"] == "s000" and first["token_ids"] == [4, 7]
    with pytest.raises(RuntimeError, match="fixture generation failure"):
        next(iterator)
    assert len(world.loaded.model.calls) == 2
    assert list(iterator) == []


@pytest.mark.multigpu
def test_rewrite_decode_base_and_adapter_share_100_inputs_on_split_model(
        tiny_model, tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-DECODE: run both paths on the real small split model."""
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model

    model_dir, model_manifest = tiny_model
    folded = b"the model\n"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/rewriter-inputs.toml").write_text(
        f'folded_prompt = "{score.build.digest(folded)}"\n', encoding="utf-8")
    kit = {"rows": [{"id": f"s{n:03d}", "text": f"the model state {n}"}
                    for n in range(100)]}
    # REWRITE-DECODE: the small-model fixture uses two tokens to exercise its cache.
    # The reference budget formula has separate exact-boundary tests.
    monkeypatch.setattr(score, "reply_budget", lambda text: 2)
    outputs, prompts, devices_seen = [], [], set()
    for adapted in (False, True):
        # REWRITE-LIMITS: the helper must establish its own layer device guards.
        with monkeypatch.context() as unpinned_load:
            unpinned_load.setattr(score.loader, "pin_layer_devices", lambda model: 0)
            loaded = score.loader.load(path=model_dir, manifest_path=model_manifest,
                                       device_map=SPLIT)
        assert loaded.devices == ["cuda:0", "cuda:1"]
        assert loaded.pinned_layers == 0
        if adapted:
            adapter = tmp_path / "adapter"
            adapted_model = get_peft_model(loaded.model, LoraConfig(
                r=16, lora_alpha=32, lora_dropout=0.0, task_type="CAUSAL_LM",
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "in_proj"]))
            adapted_model.save_pretrained(adapter)
            manifest = score.mf.build_tree(adapter, "adapter", "fixture-adapter")
            manifest.save(tmp_path / "adapter.manifest.json")
            loaded.model = adapted_model.unload()
            score.mf.require_tree(score.mf.load_tree(tmp_path / "adapter.manifest.json"),
                                  adapter)
            loaded.model = PeftModel.from_pretrained(
                loaded.model, str(adapter), is_trainable=False, local_files_only=True)
        tok = loaded.tokenizer
        tok.add_special_tokens({"eos_token": "<|im_end|>"})
        tok.chat_template = (
            "{% for m in messages %}{{ m.content + '\n' }}{% endfor %}"
            "{% if add_generation_prompt %}{{ 'model ' }}{% endif %}")
        assert tok.eos_token_id < loaded.model.get_input_embeddings().num_embeddings
        # REWRITE-DECODE: explicit greedy options must override model defaults.
        loaded.model.generation_config.do_sample = True
        loaded.model.generation_config.num_beams = 2
        loaded.model.generation_config.num_return_sequences = 2
        loaded.model.generation_config.return_dict_in_generate = True

        def observe_device(module, args):
            """REWRITE-LIMITS: inspect the CUDA device after the pin pre-hook."""
            device = next(module.parameters()).device.index
            assert torch.cuda.current_device() == device
            devices_seen.add(device)

        layers = [layer for name, module in loaded.model.named_modules()
                  if isinstance(module, torch.nn.ModuleList) and name.endswith("layers")
                  for layer in module]
        assert layers and all(not hasattr(layer, "_weaver_pinned_device") for layer in layers)
        generate = loaded.model.generate
        handles = []

        def observe_generate(*args, **kwargs):
            """REWRITE-LIMITS: register observers after the helper's pin opportunity."""
            if not handles:
                handles.extend(layer.register_forward_pre_hook(observe_device)
                               for layer in layers)
            return generate(*args, **kwargs)

        monkeypatch.setattr(loaded.model, "generate", observe_generate)
        prompts.append(score.preflight(tok, kit, folded, repo=tmp_path))
        torch.cuda.set_device(1)
        outputs.append(list(score.decode_outputs(loaded, kit, folded, repo=tmp_path)))
        assert len(handles) == len(layers)
        assert all(hasattr(layer, "_weaver_pinned_device") for layer in layers)
        for handle in handles:
            handle.remove()
        if adapted:
            assert all(not p.requires_grad for p in loaded.model.parameters())
        del loaded
    assert prompts[0] == prompts[1]
    assert devices_seen == {0, 1}
    for rows in outputs:
        assert [row["id"] for row in rows] == [row["id"] for row in kit["rows"]]
        for row in rows:
            assert 1 <= len(row["token_ids"]) <= 2
            assert row["finish_reason"] == (
                "stop" if row["token_ids"][-1] == tok.eos_token_id else "length")
            assert math.isfinite(row["wall_seconds"]) and row["wall_seconds"] > 0
    assert [{k: v for k, v in row.items() if k != "wall_seconds"} for row in outputs[0]] == [
        {k: v for k, v in row.items() if k != "wall_seconds"} for row in outputs[1]]
