"""pin_layer_devices: each decoder layer must run with its own GPU as the current device.

The race this guards against (docs/environment.md, Known gaps) does not reproduce at
stand-in sizes, but its precondition does, deterministically: which device is current
inside each layer. These tests check that precondition directly.
"""

import inspect

import pytest

from weaver_train import loader, trainer

SPLIT = {"model.embed_tokens": 0, "model.layers.0": 0, "model.layers.1": 1, "model.norm": 1, "lm_head": 0}


def test_helper_identical_in_loader_and_trainer():
    assert inspect.getsource(loader.pin_layer_devices) == inspect.getsource(trainer.pin_layer_devices)


def _current_device_per_layer(model, ids):
    import torch

    seen = {}

    def spy(i):
        def fn(mod, args):  # must return None: a pre-hook's return value replaces the args
            seen.setdefault(i, torch.cuda.current_device())
        return fn

    # registered after any pin hook, so it observes the device the pin hook set
    hooks = [layer.register_forward_pre_hook(spy(i)) for i, layer in enumerate(model.model.layers)]
    with torch.no_grad():
        model(ids)
    for h in hooks:
        h.remove()
    return seen


@pytest.mark.multigpu
@pytest.mark.parametrize("helper", [loader.pin_layer_devices, trainer.pin_layer_devices])
def test_pinning_sets_each_layers_device(tiny_model, helper):
    import torch
    from transformers import AutoModelForCausalLM

    model_dir, _ = tiny_model
    torch.cuda.set_device(0)
    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.bfloat16, device_map=SPLIT).eval()
    ids = torch.tensor([[2, 5, 6, 7, 8]], device="cuda:0")

    assert _current_device_per_layer(model, ids) == {0: 0, 1: 0}  # the bug's precondition
    assert helper(model) == 2
    assert helper(model) == 0  # idempotent
    torch.cuda.set_device(0)
    assert _current_device_per_layer(model, ids) == {0: 0, 1: 1}


@pytest.mark.multigpu
def test_loader_pins_split_models(tiny_model):
    model_dir, manifest_path = tiny_model
    lm = loader.load(manifest_path=manifest_path, path=model_dir, device_map=SPLIT)
    assert lm.devices == ["cuda:0", "cuda:1"] and lm.pinned_layers == 2


@pytest.mark.gpu
def test_loader_leaves_single_gpu_models_alone(tiny_model):
    model_dir, manifest_path = tiny_model
    lm = loader.load(manifest_path=manifest_path, path=model_dir, device_map={"": 0})
    assert lm.pinned_layers == 0
