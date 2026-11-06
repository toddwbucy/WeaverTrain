"""GRANITE-TESTS: check all approved pairs with the verified pinned tokenizer."""

import os
import tomllib
from pathlib import Path

import pytest

import rewriter_build as build
import rewriter_granite_profile as profile


def test_pinned_granite_training_boundary():
    """GRANITE-DATA: compare exact text, token prefix, loss span and final LF."""
    pairs = os.environ.get("WEAVER_GRANITE_TEST_PAIRS")
    folded = os.environ.get("WEAVER_GRANITE_TEST_FOLDED")
    if not pairs or not folded:
        pytest.skip("needs the verified local pair tree and folded prompt for pinned Granite")
    evidence = build.Evidence(build.REPO)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    indexed, _, record, _, _ = build.read_pair_directory(pairs, evidence, pins)
    selection = record["balance"]["selections"]["r1"]
    assert selection["count"] == 200
    assert selection["ordered_ids_sha256"] == pins["r1_ordered_ids"]
    system = build.folded_system(Path(folded).read_bytes(), pins["folded_prompt"])
    tokenizer, manifest, directory, _ = build.build_tokenizer(evidence, profile.MODEL)
    totals = {"pairs": 0, "all_tokens": 0, "final_target_tokens": 0, "max_sequence": 0}
    for key in selection["ids"]:
        pair = indexed[tuple(key)]
        rendered = build.render_pair(tokenizer, pair, system, model=profile.MODEL)
        prefix = tokenizer.apply_chat_template(build.initial_messages(system, pair["input"]),
            tokenize=True, return_dict=False, add_generation_prompt=True, enable_thinking=False)
        assert rendered["input_ids"][:len(prefix)] == prefix
        assert rendered["assistant_masks"][:len(prefix)] == [0] * len(prefix)
        loss_ids = rendered["input_ids"][len(prefix):]
        assert rendered["assistant_masks"][len(prefix):] == [1] * len(loss_ids)
        assert tokenizer.decode(loss_ids, skip_special_tokens=False,
            clean_up_tokenization_spaces=False) == pair["target"] + profile.ENDING
        totals["pairs"] += 1
        totals["all_tokens"] += len(rendered["input_ids"])
        totals["final_target_tokens"] += len(loss_ids)
        totals["max_sequence"] = max(totals["max_sequence"], len(rendered["input_ids"]))
    evidence.verify()
    build.mf.require_tree(manifest, directory)
    print(totals)
