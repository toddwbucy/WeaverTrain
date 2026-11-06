"""GRANITE-MEMORY: one full-target LoRA step at the approved 49,152 tokens."""

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import rewriter_granite as granite

profile, score, mf, e4 = granite.profile, granite.score, granite.mf, granite.e4


def check_counts(inventory, trainable):
    """GRANITE-TARGETS: check measured counts before the full-size optimizer step."""
    from weaver_train.trainer import TargetCoverageError

    if len(inventory) != 448 or trainable != 140509184:
        raise TargetCoverageError("GRANITE-TARGETS: pinned module or parameter count differs")


def probe(out_dir, *, repo=granite.REPO):
    """GRANITE-MEMORY, GRANITE-RECORD: retain success or failure without a retry."""
    imports = granite.provenance.capture(repo)
    commit = imports["commit"]
    config, config_sha = granite.configuration(repo)
    manifest_path = score.loader.manifest_path_for(profile.MODEL)
    manifest = mf.load_tree(manifest_path)
    directory = score.loader.resolve_dir(manifest)
    profile.verify_model_files(manifest, directory)
    granite.require_cache(manifest, directory)
    out, _ = score.fresh_score_output(out_dir, repo=repo, inputs=(directory,))
    out.mkdir(parents=True)
    record = {"schema": "weaver-train/rewriter-granite-probe/v1", "status": "started",
        **profile.IDENTITY, "commit": commit, "engine": "python-hf", "precision": "bf16",
        "created_at": datetime.now(timezone.utc).isoformat(), "tree_hash": manifest.tree_hash,
        "config_sha256": config_sha, "sequence_length": 49152, "imports_before": imports}
    started, devices = time.monotonic(), []
    try:
        (out / "procedure.json").write_bytes(e4.json_bytes(record))
        record["verification"] = granite.verify_environment(out, repo)
        import unsloth
        import torch
        from weaver_train import trainer

        granite.require(torch.cuda.is_available() and torch.cuda.device_count() == 2,
                        "GRANITE-VERIFY: exactly two selected GPUs are required")
        torch.cuda.init()
        devices = list(range(2))
        for device in devices:
            torch.cuda.reset_peak_memory_stats(device)
        model, _ = unsloth.FastLanguageModel.from_pretrained(
            model_name=str(directory), max_seq_length=49152, dtype=torch.bfloat16,
            load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
            device_map="balanced", offload_embedding=False, trust_remote_code=False,
            local_files_only=True)
        record["placement_before"] = granite.loaded_placement(model)
        inventory = trainer.granite_inventory(model, profile.TARGETS, 16)
        check_counts(inventory, sum(row["lora_parameters"] for row in inventory))
        record["target_inventory"] = inventory
        model = unsloth.FastLanguageModel.get_peft_model(
            model, r=16, lora_alpha=32, lora_dropout=0.0, target_modules=profile.TARGETS,
            bias="none", use_gradient_checkpointing="unsloth", random_state=0)
        trainer.pin_layer_devices(model)
        count = trainer.check_granite_adapter(model, inventory, 16)
        check_counts(inventory, count)
        record.update(trainable_parameters=count, component_coverage=trainer.component_coverage(model),
                      placement_after=granite.loaded_placement(model))
        trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
        optimizer = torch.optim.AdamW(trainable, lr=config["train"]["learning_rate"])
        vocab = model.get_input_embeddings().weight.shape[0]
        ids = torch.randint(100, vocab - 100, (1, 49152), generator=torch.Generator().manual_seed(0))
        ids = ids.to(model.get_input_embeddings().weight.device)
        model.train()
        for device in devices:
            torch.cuda.synchronize(device)
        step_start = time.monotonic()
        with torch.autocast("cuda", dtype=torch.bfloat16):
            loss = model(input_ids=ids, labels=ids).loss
        granite.require(torch.isfinite(loss).item(), "GRANITE-MEMORY: non-finite probe loss")
        loss.backward()
        granite.require(all(p.grad is not None and torch.isfinite(p.grad).all().item()
                            for p in trainable), "GRANITE-MEMORY: missing or non-finite probe gradient")
        record["finite_gradients"] = True
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        for device in devices:
            torch.cuda.synchronize(device)
        record["steps"] = [{"step": 1, "seconds": time.monotonic() - step_start,
                            "loss": loss.item()}]
        granite.loaded_placement(model)
        model.save_pretrained(out / "adapter")
        trainer.check_adapter_only(out / "adapter")
        adapter = mf.build_tree(out / "adapter", "model", "granite-memory-probe-adapter")
        adapter.save(out / "adapter.manifest.json")
        mf.require_tree(adapter, out / "adapter")
        record["adapter_tree_hash"] = adapter.tree_hash
        mf.require_tree(manifest, directory)
        record["imports_after"] = granite.provenance.capture(repo, previous=imports)
        granite.require(granite.configuration(repo)[1] == config_sha,
                        "GRANITE-RECORD: configuration changed during the probe")
        record["status"] = "completed"
    except BaseException as exc:
        record.update(status="failed", failure={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        record["wall_seconds"] = time.monotonic() - started
        record["peak_memory_bytes"] = {str(device): {
            "allocated": torch.cuda.max_memory_allocated(device),
            "reserved": torch.cuda.max_memory_reserved(device)} for device in devices}
        (out / "procedure.json").write_bytes(e4.json_bytes(record))
    return record, granite.seal_procedure(out, record, "ste-rewriter-granite-probe")


def main(argv=None):
    """GRANITE-MEMORY: this entry point runs only the memory procedure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    probe(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
