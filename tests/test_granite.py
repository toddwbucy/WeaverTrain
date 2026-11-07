"""GRANITE-TESTS: dense native and Unsloth paths use a local random stand-in."""

import json

import pytest

from weaver_train import loader, manifest as mf, trainer


TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
SPLIT = {"model.embed_tokens": 0, "model.layers.0": 0,
         "model.layers.1": 1, "model.norm": 1, "lm_head": 1}


@pytest.fixture
def tiny_granite(tmp_path):
    """GRANITE-MODEL: preserve the untied weights of the pinned architecture."""
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import GraniteConfig, GraniteForCausalLM, PreTrainedTokenizerFast

    directory = tmp_path / "tiny-granite"
    vocab = {word: i for i, word in enumerate(
        ["<pad>", "<unk>", "<s>", "</s>", "the", "model", "state", "train"])}
    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=tok, pad_token="<pad>",
        unk_token="<unk>", bos_token="<s>", eos_token="</s>")
    config = GraniteConfig(vocab_size=64, hidden_size=64, intermediate_size=128,
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
        max_position_embeddings=4096, tie_word_embeddings=False,
        attention_multiplier=0.25, embedding_multiplier=1.0,
        residual_multiplier=1.0, logits_scaling=1.0,
        pad_token_id=0, bos_token_id=2, eos_token_id=3)
    torch.manual_seed(0)
    model = GraniteForCausalLM(config).to(torch.bfloat16)
    model.save_pretrained(directory)
    tokenizer.save_pretrained(directory)
    path = tmp_path / "tiny-granite.manifest.json"
    mf.build_tree(directory, "model", "tiny-granite").save(path)
    return directory, path


@pytest.mark.multigpu
def test_granite_native_split_bf16(tiny_granite):
    """GRANITE-VERIFY: native forward and greedy decode span the selected GPUs."""
    import torch
    from transformers import GraniteForCausalLM

    directory, manifest_path = tiny_granite
    loaded = loader.load(path=directory, manifest_path=manifest_path, device_map=SPLIT)
    model = loaded.model
    assert isinstance(model, GraniteForCausalLM)
    assert model.config.model_type == "granite"
    assert loaded.devices == ["cuda:0", "cuda:1"]
    assert loaded.pinned_layers == 2
    assert {p.dtype for p in model.parameters()} == {torch.bfloat16}
    assert model.get_input_embeddings().weight is not model.get_output_embeddings().weight
    ids = torch.tensor([[4, 5, 6, 7]], device="cuda:0")
    with torch.no_grad():
        assert torch.isfinite(model(ids).logits).all()
        output = model.generate(ids, max_new_tokens=2, do_sample=False)
    assert output.shape[1] > ids.shape[1]
    mf.require_tree(mf.load_tree(manifest_path), directory)


@pytest.mark.gpu
def test_granite_unsloth_four_epochs(tiny_granite, tmp_path):
    """GRANITE-R1: the real trainer saves four adapters and complete step logs."""
    from tests.test_trainer import make_tokenized

    directory, manifest_path = tiny_granite
    model_hash = mf.load_tree(manifest_path).tree_hash
    data, _, _ = make_tokenized(tmp_path, model_hash, length=64, row_count=2)
    config = tmp_path / "granite.toml"
    config.write_text('''[lora]
r = 16
alpha = 32
dropout = 0.0
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

[train]
max_seq_length = 64
learning_rate = 1e-4
num_train_epochs = 4
per_device_batch_size = 1
gradient_accumulation_steps = 2
warmup_steps = 1
lr_scheduler = "cosine"
seed = 0
gradient_checkpointing = "unsloth"
logging_steps = 1
device_map = "balanced"

[data]
format = "tokenized"
''')
    out = tmp_path / "training"
    record = trainer.train(directory, manifest_path, data, config, out)
    assert record.status == "completed"
    assert record.results["global_step"] == 4
    assert record.results["lora_coverage"] == {
        target: {"present": 2, "adapted": 2} for target in TARGETS}
    assert [entry["epoch"] for entry in record.results["epoch_adapters"]] == [1, 2, 3, 4]
    assert [entry["step"] for entry in record.results["loss_history"]] == [1, 2, 3, 4]
    logs = json.loads((out / "trainer/log_history.json").read_text())
    assert [row["step"] for row in logs if "learning_rate" in row] == [1, 2, 3, 4]
    for epoch in range(1, 5):
        mf.require_tree(mf.load_tree(out / f"adapter-epoch-{epoch}.manifest.json"),
                        out / f"adapter-epoch-{epoch}")
    mf.require_tree(mf.load_tree(manifest_path), directory)


@pytest.mark.multigpu
def test_granite_unsloth_split_weights_and_gradients(tiny_granite):
    """GRANITE-VERIFY: the installed training path preserves untied bf16 weights."""
    import unsloth
    import torch
    from safetensors.torch import load_file

    directory, manifest_path = tiny_granite
    original = load_file(directory / "model.safetensors")
    model, _ = unsloth.FastLanguageModel.from_pretrained(
        model_name=str(directory), max_seq_length=64, dtype=torch.bfloat16,
        load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
        device_map=SPLIT, offload_embedding=False,
        trust_remote_code=False, local_files_only=True)
    assert model.config.model_type == "granite"
    assert model.config.tie_word_embeddings is False
    assert model.get_input_embeddings().weight is not model.get_output_embeddings().weight
    for name, parameter in model.named_parameters():
        assert parameter.dtype == torch.bfloat16
        assert torch.equal(parameter.detach().cpu(), original[name]), name
    model = unsloth.FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=32, lora_dropout=0.0,
        target_modules=TARGETS, bias="none", use_gradient_checkpointing="unsloth",
        random_state=0)
    trainer.pin_layer_devices(model)
    assert {str(p.device) for p in model.parameters()} == {"cuda:0", "cuda:1"}
    trainer.check_coverage(trainer.target_coverage(model, TARGETS))
    ids = torch.tensor([[4, 5, 6, 7, 4, 5, 6, 7]], device="cuda:0")
    model.train()
    with torch.autocast("cuda", dtype=torch.bfloat16):
        loss = model(input_ids=ids, labels=ids).loss
    assert torch.isfinite(loss)
    loss.backward()
    gradients = [p.grad for p in model.parameters() if p.requires_grad]
    assert gradients and all(g is not None and torch.isfinite(g).all() for g in gradients)
    mf.require_tree(mf.load_tree(manifest_path), directory)


@pytest.mark.multigpu
def test_granite_padding_substitution_keeps_assistant_labels(tiny_granite):
    """GRANITE-VERIFY: real Unsloth padding repair leaves the assistant-mask labels intact."""
    import unsloth
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast
    import rewriter_granite_profile as profile

    directory, manifest_path = tiny_granite
    vocab = {word: i for i, word in enumerate([
        "zero", "<unk>", "<s>", "</s>", "the", "model", "state", "train", "<|fim_pad|>"])}
    backend = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="</s>",
        eos_token="</s>", bos_token="<s>", unk_token="<unk>",
        additional_special_tokens=["<|fim_pad|>"])
    tokenizer.save_pretrained(directory)
    configs = []
    for filename in ("config.json", "generation_config.json"):
        path = directory / filename
        config = json.loads(path.read_bytes())
        config["pad_token_id"] = 3
        path.write_text(json.dumps(config))
        configs.append(config)
    manifest = mf.build_tree(directory, "model", "tiny-granite")
    manifest.save(manifest_path)
    before = profile.padding_ids(tokenizer, *configs)
    model, tokenizer = unsloth.FastLanguageModel.from_pretrained(
        model_name=str(directory), max_seq_length=64, dtype=torch.bfloat16,
        load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
        device_map=SPLIT, offload_embedding=False,
        trust_remote_code=False, local_files_only=True)
    trainer.pin_layer_devices(model)
    after = profile.padding_ids(tokenizer, model.config.to_dict(), model.generation_config.to_dict())
    assert before == {"tokenizer_pad_token_id": 3, "tokenizer_eos_token_id": 3,
        "model_pad_token_id": 3, "model_eos_token_id": 3,
        "generation_pad_token_id": 3, "generation_eos_token_id": 3}
    assert after == {"tokenizer_pad_token_id": 8, "tokenizer_eos_token_id": 3,
        "model_pad_token_id": 8, "model_eos_token_id": 3,
        "generation_pad_token_id": 8, "generation_eos_token_id": 3}
    batch = trainer.AssistantCollator()([
        {"input_ids": [4, 5, 3, 8], "assistant_masks": [0, 1, 1, 1]}])
    assert batch["labels"].tolist() == [[-100, 5, 3, 8]]
    assert batch["input_ids"].tolist() == [[4, 5, 3, 8]]
    assert batch["attention_mask"].tolist() == [[1, 1, 1, 1]]
    mf.require_tree(manifest, directory)
