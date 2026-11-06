"""GRANITE-TESTS: each refusal checks its own reason with valid surrounding inputs."""

import json
from types import SimpleNamespace

import pytest

import rewriter_granite as granite
import rewriter_granite_probe as probe
import rewriter_granite_profile as profile
from weaver_train import manifest as mf, trainer
from tests.test_granite import tiny_granite


@pytest.mark.parametrize("field", list(profile.IDENTITY))
def test_profile_rejects_changed_identity(field):
    value = dict(profile.IDENTITY, procedure=profile.R1)
    value[field] = "different"
    with pytest.raises(ValueError, match="series, model or tokenizer identity differs"):
        profile.model_for(value)


def test_nemotron_refuses_granite_identity():
    with pytest.raises(ValueError, match="another model cannot be a Nemotron reference"):
        profile.model_for({"model": profile.MODEL})
    assert profile.model_for({}) == granite.build.MODEL
    assert profile.model_for(profile.IDENTITY) == profile.MODEL


@pytest.fixture
def fake_tokenizer(monkeypatch):
    class Tokenizer:
        template = "fixture-template"
        eos_token_id = profile.END_ID
        end = profile.END_ID
        header = profile.HEADER
        ending = profile.ENDING

        def get_chat_template(self):
            return self.template

        def convert_tokens_to_ids(self, value):
            return self.end

        def apply_chat_template(self, messages, *, add_generation_prompt, **options):
            assert options == {"tokenize": False, "enable_thinking": False}
            return "fixture prefix" + self.header + (
                "" if add_generation_prompt else messages[-1]["content"] + self.ending)

    value = Tokenizer()
    monkeypatch.setattr(profile, "TEMPLATE", granite.build.digest(value.template.encode()))
    return value


@pytest.mark.parametrize("field,value,reason", [
    ("template", "wrong", "loaded chat template differs"),
    ("end", 1, "loaded end-of-turn token differs"),
    ("eos_token_id", 1, "loaded end-of-turn token differs"),
    ("header", profile.HEADER + "\n", "assistant header differs"),
    ("ending", "<|im_end|>", "final target, end-of-turn or final LF differs"),
])
def test_granite_template_and_boundary_rejections(fake_tokenizer, field, value, reason):
    setattr(fake_tokenizer, field, value)
    with pytest.raises(ValueError, match=reason):
        profile.verify_render(fake_tokenizer, [{"role": "user", "content": "source"}], "target")


def test_granite_thinking_prefix_is_retained(fake_tokenizer):
    result = profile.verify_render(fake_tokenizer, [{"role": "user", "content": "source"}], "target")
    assert result.endswith(profile.HEADER)


@pytest.fixture
def model_files(tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    (root / "chat_template.jinja").write_text("fixture template")
    (root / "tokenizer_config.json").write_text("{}")
    (root / "config.json").write_text(json.dumps({"model_type": "granite",
        "architectures": ["GraniteForCausalLM"], "eos_token_id": profile.END_ID}))
    monkeypatch.setattr(profile, "TEMPLATE", mf.sha256_file(root / "chat_template.jinja"))
    monkeypatch.setattr(profile, "TOKENIZER_CONFIG", mf.sha256_file(root / "tokenizer_config.json"))
    tree = mf.build_tree(root, "model", profile.MODEL,
                         source={"repo_id": profile.REPO_ID, "revision": profile.REVISION})
    return root, tree


@pytest.mark.parametrize("field", ["name", "kind", "source"])
def test_pinned_model_identity_guard(model_files, field):
    root, tree = model_files
    setattr(tree, field, {} if field == "source" else "wrong")
    with pytest.raises(ValueError, match="pinned model identity differs"):
        profile.verify_model_files(tree, root)


def test_model_file_pin_guard(model_files, monkeypatch):
    root, tree = model_files
    monkeypatch.setattr(profile, "TOKENIZER_CONFIG", "wrong")
    with pytest.raises(ValueError, match="template or tokenizer configuration pin differs"):
        profile.verify_model_files(tree, root)


@pytest.mark.parametrize("field,value,reason", [
    ("model_type", "llama", "native dense architecture differs"),
    ("architectures", ["NemotronHForCausalLM"], "native dense architecture differs"),
    ("eos_token_id", 3, "configured end-of-turn token differs"),
])
def test_model_config_guard(model_files, field, value, reason):
    root, tree = model_files
    config = json.loads((root / "config.json").read_bytes())
    config[field] = value
    (root / "config.json").write_text(json.dumps(config))
    tree = mf.build_tree(root, tree.kind, tree.name, source=tree.source)
    with pytest.raises(ValueError, match=reason):
        profile.verify_model_files(tree, root)


def test_fast_load_copy_is_required(model_files, monkeypatch):
    root, tree = model_files
    tree.local_dir_name = root.name
    monkeypatch.delenv("WEAVER_TRAIN_MODEL_CACHE", raising=False)
    with pytest.raises(ValueError, match="verified fast-load copy is required"):
        granite.require_cache(tree, root)
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_CACHE", str(root.parent))
    granite.require_cache(tree, root)


@pytest.fixture
def adapters(tiny_granite):
    import torch
    from transformers import GraniteForCausalLM
    from peft import LoraConfig, get_peft_model

    root, _ = tiny_granite
    model = GraniteForCausalLM.from_pretrained(root, dtype=torch.bfloat16)
    inventory = trainer.granite_inventory(model, profile.TARGETS, 16)
    adapted = get_peft_model(model, LoraConfig(r=16, lora_alpha=32,
        lora_dropout=0.0, target_modules=profile.TARGETS, bias="none"))
    return adapted, inventory


def test_full_dense_inventory_and_parameter_formula(adapters):
    model, inventory = adapters
    assert len(inventory) == 14
    count = trainer.check_granite_adapter(model, inventory, 16)
    assert count == sum(16 * (row["in_features"] + row["out_features"]) for row in inventory)
    assert all("embed" not in row["name"] and "lm_head" not in row["name"] for row in inventory)


@pytest.mark.parametrize("change,reason", [
    ("missing", "missing or extra adapter"),
    ("frozen", "adapter shape or training flag differs"),
    ("shape", "adapter shape or training flag differs"),
    ("outside", "trainable parameters differ from the inventoried adapters"),
    ("count", "trainable parameter count differs"),
])
def test_each_adapter_and_trainable_parameter_guard(adapters, change, reason):
    import torch

    model, inventory = adapters
    module = dict(model.named_modules())["base_model.model." + inventory[0]["name"]]
    if change == "missing":
        del module.lora_A["default"]
    elif change == "frozen":
        module.lora_A["default"].weight.requires_grad_(False)
    elif change == "shape":
        module.lora_A["default"].weight = torch.nn.Parameter(torch.zeros(3, 4))
    elif change == "outside":
        model.get_output_embeddings().weight.requires_grad_(True)
    else:
        inventory[0]["lora_parameters"] += 1
    with pytest.raises(trainer.TargetCoverageError, match=reason):
        trainer.check_granite_adapter(model, inventory, 16)


@pytest.mark.parametrize("wrong_targets", [True, False])
def test_inventory_refuses_wrong_targets_or_missing_matrix(tiny_granite, wrong_targets):
    from transformers import GraniteForCausalLM

    model = GraniteForCausalLM.from_pretrained(tiny_granite[0])
    targets = profile.TARGETS[:-1] if wrong_targets else profile.TARGETS
    reason = "seven target names differ" if wrong_targets else "missing base linear"
    if not wrong_targets:
        del model.model.layers[1].mlp.down_proj
    with pytest.raises(trainer.TargetCoverageError, match=reason):
        trainer.granite_inventory(model, targets, 16)


@pytest.mark.parametrize("modules,count", [(447, 140509184), (448, 6648832)])
def test_pinned_target_count_guard(modules, count):
    with pytest.raises(trainer.TargetCoverageError, match="pinned module or parameter count differs"):
        probe.check_counts([{}] * modules, count)


def test_bf16_weight_guard(monkeypatch):
    import torch

    parameter = SimpleNamespace(device=torch.device("cuda:0"), dtype=torch.float32,
                                is_floating_point=lambda: True)
    model = SimpleNamespace(parameters=lambda: [parameter], named_parameters=lambda: [("weight", parameter)])
    with pytest.raises(RuntimeError, match="reference weights are not bf16"):
        trainer.granite_placement(model)


@pytest.mark.parametrize("selection", [None, "2", "1,0"])
def test_environment_refuses_wrong_gpu_selection(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(granite.provenance, "clean_commit", lambda repo: {"rev": "x", "dirty": False})
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", selection or "")
    with pytest.raises(ValueError, match="approved GPU selection from .env"):
        granite.verify_environment(tmp_path, tmp_path)


def test_environment_failure_is_retained_without_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(granite.provenance, "clean_commit", lambda repo: {"rev": "x", "dirty": False})
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    calls = []

    def failed(*args, **kwargs):
        calls.append(args)
        kwargs["stdout"].write(b"fixture failure\n")
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(granite.subprocess, "run", failed)
    with pytest.raises(ValueError, match="fresh environment verification failed"):
        granite.verify_environment(tmp_path, tmp_path)
    record = json.loads((tmp_path / "verification.json").read_bytes())
    assert record["status"] == "failed" and record["exit_code"] == 17
    assert len(calls) == 1


def test_environment_commit_guard(tmp_path, monkeypatch):
    commits = iter([{"rev": "one", "dirty": False}, {"rev": "two", "dirty": False}])
    monkeypatch.setattr(granite.provenance, "clean_commit", lambda repo: next(commits))
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    monkeypatch.setattr(granite.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    with pytest.raises(ValueError, match="checkout changed during environment verification"):
        granite.verify_environment(tmp_path, tmp_path)


@pytest.mark.parametrize("wrong_class", [True, False])
def test_loaded_native_class_and_two_gpu_guards(monkeypatch, wrong_class):
    name = "OtherForCausalLM" if wrong_class else "GraniteForCausalLM"
    model = type(name, (), {"config": SimpleNamespace(model_type="granite")})()
    devices = {"weight": "cuda:0"} if not wrong_class else {"a": "cuda:0", "b": "cuda:1"}
    monkeypatch.setattr(trainer, "granite_placement", lambda model: {"parameter_devices": devices})
    reason = "not native dense Granite" if wrong_class else "exactly the two selected GPUs"
    with pytest.raises(ValueError, match=reason):
        granite.loaded_placement(model)


@pytest.mark.parametrize("wrong_family", [True, False])
def test_training_checks_pinned_counts_before_attachment(tiny_granite, tmp_path, monkeypatch, wrong_family):
    import unsloth
    import torch
    from transformers import GraniteForCausalLM

    model = GraniteForCausalLM.from_pretrained(tiny_granite[0], dtype=torch.bfloat16)
    if wrong_family:
        model.config.model_type = "different"
    monkeypatch.setattr(unsloth.FastLanguageModel, "from_pretrained", lambda **kw: (model, None))
    monkeypatch.setattr(trainer, "require_gpu_layers", lambda model: None)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    cfg = trainer.load_config(granite.REPO / granite.CONFIG)
    metrics = {"required_target_counts": {"modules": 448, "parameters": 140509184}}
    reason = "implementation is not dense Granite" if wrong_family else "pinned module or parameter count differs"
    with pytest.raises(trainer.TargetCoverageError, match=reason):
        trainer._run_unsloth(tiny_granite[0], b"", cfg, tmp_path / "unused", metrics)


def test_training_requires_probe_placement(tiny_granite, tmp_path, monkeypatch):
    import unsloth
    import torch
    from transformers import GraniteForCausalLM

    model = GraniteForCausalLM.from_pretrained(tiny_granite[0], dtype=torch.bfloat16)
    monkeypatch.setattr(unsloth.FastLanguageModel, "from_pretrained", lambda **kw: (model, None))
    monkeypatch.setattr(trainer, "require_gpu_layers", lambda model: None)
    monkeypatch.setattr(trainer, "granite_placement", lambda model: {"attention_implementation": "different"})
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    cfg = trainer.load_config(granite.REPO / granite.CONFIG)
    with pytest.raises(RuntimeError, match="training placement or attention path differs from the probe"):
        trainer._run_unsloth(tiny_granite[0], b"", cfg, tmp_path / "unused",
                             {"required_granite_placement": {"attention_implementation": "approved"}})
