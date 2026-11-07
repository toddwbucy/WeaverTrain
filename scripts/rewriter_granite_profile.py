"""GRANITE-MODEL and GRANITE-DATA: the separate dense series contract."""

import hashlib
import json

from weaver_train import manifest as mf

MODEL = "granite-4.2-30b"
REPO_ID = "ibm-granite/granite-4.2-30b"
REVISION = "9e668ce1c538387ef24d3644e9b0606647762636"
TEMPLATE = "f0ba43f79b3cabca5e5a7584c77aec92f49feae45cdf7779c87d9fc54cd90258"
TOKENIZER_CONFIG = "2007f26df166ed10a2d6fa881c1c8f3cdd151e451469f59b274897f14449835f"
HEADER = "<|im_start|>assistant\n<think></think>"
ENDING = "<|im_end|>\n"
END_ID = 100257
R0, R1 = "granite-r0", "granite-r1"
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
IDENTITY = {"series": "granite", "model": MODEL, "tokenizer_config_sha256": TOKENIZER_CONFIG,
            "engine": "python-hf", "precision": "bf16"}


def require(condition, reason):
    """GRANITE-VERIFY: refuse a changed contract without a fallback."""
    if not condition:
        raise ValueError(reason)


def model_for(record, default="nemotron-3-nano-30b-a3b"):
    """GRANITE-SCOPE: new artifacts identify their series and cannot cross models."""
    if record.get("series") == "granite" or record.get("procedure") in (R0, R1):
        require(all(record.get(key) == value for key, value in IDENTITY.items()),
                "GRANITE-MODEL: series, model or tokenizer identity differs")
        return MODEL
    require(record.get("series") is None and record.get("model", default) == default,
            "GRANITE-SCOPE: an artifact from another model cannot be a Nemotron reference")
    return default


def verify_model_files(manifest, directory):
    """GRANITE-MODEL, GRANITE-DATA: check the manifest pins before tokenizer use."""
    require(manifest.name == MODEL and manifest.kind == "model" and
            manifest.source == {"repo_id": REPO_ID, "revision": REVISION},
            "GRANITE-MODEL: pinned model identity differs")
    files = {entry.path: entry.sha256 for entry in manifest.files}
    require(files.get("chat_template.jinja") == TEMPLATE and
            files.get("tokenizer_config.json") == TOKENIZER_CONFIG,
            "GRANITE-DATA: template or tokenizer configuration pin differs")
    mf.require_tree(manifest, directory)
    config = json.loads((directory / "config.json").read_bytes())
    require(config.get("model_type") == "granite" and
            config.get("architectures") == ["GraniteForCausalLM"],
            "GRANITE-MODEL: native dense architecture differs")
    require(config.get("eos_token_id") == END_ID,
            "GRANITE-DATA: configured end-of-turn token differs")


def verify_tokenizer(tokenizer):
    """GRANITE-DATA: the actual template and stop token must equal the pins."""
    digest = hashlib.sha256(tokenizer.get_chat_template().encode()).hexdigest()
    require(digest == TEMPLATE, "GRANITE-DATA: loaded chat template differs")
    require(tokenizer.convert_tokens_to_ids("<|im_end|>") == END_ID and
            tokenizer.eos_token_id == END_ID,
            "GRANITE-DATA: loaded end-of-turn token differs")


def padding_ids(tokenizer, model_config, generation_config):
    """GRANITE-VERIFY: capture actual tokenizer and configuration token IDs."""
    from weaver_train.trainer import granite_padding_ids

    return granite_padding_ids(tokenizer, model_config, generation_config)


def verify_padding_ids(values, *, loaded):
    """GRANITE-VERIFY: permit only the approved padding substitution at load."""
    from weaver_train.trainer import check_granite_padding_ids

    check_granite_padding_ids(values, loaded=loaded)


def verify_render(tokenizer, messages, target=None):
    """GRANITE-DATA: preserve the exact thinking-off prefix and final LF."""
    verify_tokenizer(tokenizer)
    options = {"tokenize": False, "enable_thinking": False}
    prefix = tokenizer.apply_chat_template(messages, add_generation_prompt=True, **options)
    require(prefix.endswith(HEADER), "GRANITE-DATA: assistant header differs")
    if target is not None:
        complete = tokenizer.apply_chat_template(
            messages + [{"role": "assistant", "content": target}],
            add_generation_prompt=False, **options)
        require(complete == prefix + target + ENDING,
                "GRANITE-DATA: final target, end-of-turn or final LF differs")
    return prefix
