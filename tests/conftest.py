"""Shared fixtures. The stand-in model is built here, never downloaded.

``tiny_model`` is a randomly initialised GraniteMoeHybrid (one Mamba-2 layer, one
attention layer, a 4-expert MoE) with a word-level tokenizer, saved in bf16. It
exercises the same transformers code and mamba-ssm / causal-conv1d kernels as the
30B models at a few hundred KB, so tests need neither the reference weights nor
any training data.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from weaver_train import manifest as mf

WORDS = ("the a model layer state token train test bench run loss step value key "
         "query mamba attention expert route gate cache shard device memory").split()
SPECIALS = ["<pad>", "<unk>", "<s>", "</s>"]


def _have_gpus(n: int) -> bool:
    try:
        import torch
    except ImportError:
        return False
    return torch.cuda.is_available() and torch.cuda.device_count() >= n


def pytest_collection_modifyitems(config, items):
    gpus = int(os.environ.get("WEAVER_TRAIN_EXPECT_GPUS", "1"))
    no_gpu = pytest.mark.skip(reason="needs CUDA GPU(s)")
    for item in items:
        need = 2 if "multigpu" in item.keywords else 1
        if ("gpu" in item.keywords or "multigpu" in item.keywords) and not _have_gpus(min(need, gpus)):
            item.add_marker(no_gpu)


def build_tiny_model(dest: Path) -> None:
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import GraniteMoeHybridConfig, GraniteMoeHybridForCausalLM, PreTrainedTokenizerFast

    vocab = {t: i for i, t in enumerate(SPECIALS + WORDS)}
    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    hf_tok = PreTrainedTokenizerFast(tokenizer_object=tok, pad_token="<pad>", unk_token="<unk>",
                                     bos_token="<s>", eos_token="</s>")
    hf_tok.model_max_length = 4096

    cfg = GraniteMoeHybridConfig(
        vocab_size=64, hidden_size=64, intermediate_size=32, shared_intermediate_size=64,
        num_hidden_layers=2, layer_types=["mamba", "attention"],
        num_attention_heads=4, num_key_value_heads=2, position_embedding_type="nope",
        num_local_experts=4, num_experts_per_tok=2,
        mamba_n_heads=8, mamba_d_head=16, mamba_d_state=16, mamba_n_groups=1,
        mamba_d_conv=4, mamba_expand=2, mamba_chunk_size=32,
        max_position_embeddings=4096, tie_word_embeddings=True,
        pad_token_id=vocab["<pad>"], bos_token_id=vocab["<s>"], eos_token_id=vocab["</s>"],
    )
    torch.manual_seed(0)
    model = GraniteMoeHybridForCausalLM(cfg).to(torch.bfloat16)
    model.save_pretrained(dest)
    hf_tok.save_pretrained(dest)


@pytest.fixture(scope="session")
def tiny_model_src(tmp_path_factory) -> Path:
    """The stand-in model, built once per session. Do not modify; copy it instead."""
    dest = tmp_path_factory.mktemp("tiny-granite-hybrid")
    build_tiny_model(dest)
    return dest


@pytest.fixture
def tiny_model(tiny_model_src, tmp_path) -> tuple[Path, Path]:
    """A private copy of the stand-in model and its manifest: (model_dir, manifest_path)."""
    import shutil

    model_dir = tmp_path / "tiny-granite-hybrid"
    shutil.copytree(tiny_model_src, model_dir)
    m = mf.build_tree(model_dir, "model", "tiny-granite-hybrid", local_dir_name=model_dir.name)
    manifest_path = tmp_path / "tiny-granite-hybrid.json"
    m.save(manifest_path)
    return model_dir, manifest_path
