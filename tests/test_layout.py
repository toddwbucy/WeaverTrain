"""The repository root is on sys.path under pytest and scripts, so a top-level directory
can stand in for a package of the same name. A `vllm/` directory did that on
2026-10-31: Unsloth probes find_spec("vllm"), found the directory, and its import failed.
"""

import importlib.util
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# packages libraries probe for that are deliberately not installed in the training env
PROBED = {"vllm", "flash_attn", "deepspeed", "bitsandbytes", "xformers", "apex"}


def installed_module_names() -> set[str]:
    site = Path(sysconfig.get_paths()["purelib"])
    return {p.name.removesuffix(".py") for p in site.iterdir()
            if not p.name.endswith((".dist-info", ".pth"))}


def test_no_top_level_directory_shadows_a_package():
    taken = installed_module_names() | PROBED
    shadows = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name in taken)
    assert shadows == [], f"rename to a non-identifier (e.g. with a hyphen): {shadows}"


def test_vllm_is_not_importable_in_the_training_env():
    assert importlib.util.find_spec("vllm") is None
