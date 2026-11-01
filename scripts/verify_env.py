"""Verify the weaver-train environment: driver, GPUs, kernels, framework stack.

Run by scripts/setup-env.sh after `uv sync`. Exits non-zero on the first failed
check, so setup stops instead of continuing on a degraded stack. On success prints
a JSON report (versions, GPUs, check results) to stdout, or writes it to --report.

Every kernel check runs the CUDA kernel and compares it with the package's own
PyTorch reference, so a kernel that imports but computes wrong values fails here.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import inspect
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

CHECKS: list[dict] = []


class CheckFailed(Exception):
    pass


def check(name):
    def deco(fn):
        def run(*a, **kw):
            try:
                detail = fn(*a, **kw)
            except Exception as e:  # noqa: BLE001 - report every failure the same way
                CHECKS.append({"check": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})
                raise CheckFailed(name) from e
            CHECKS.append({"check": name, "ok": True, "detail": detail})
            return detail

        return run

    return deco


def max_err(a, b):
    """Largest absolute difference; a non-finite value on either side is a failure,
    since NaN compares false against every tolerance."""
    import torch

    a, b = a.float(), b.float()
    if not (torch.isfinite(a).all() and torch.isfinite(b).all()):
        raise RuntimeError("non-finite values in kernel output or reference")
    return (a - b).abs().max().item()


@check("driver")
def check_driver():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.split()[0]
    return {"driver_version": out}


@check("torch_cuda")
def check_torch_cuda(expect_gpus: int, expect_name: str | None):
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False")
    n = torch.cuda.device_count()
    gpus = []
    for i in range(n):
        p = torch.cuda.get_device_properties(i)
        gpus.append({"index": i, "name": p.name, "capability": f"{p.major}.{p.minor}",
                     "memory_gib": round(p.total_memory / 2**30, 1)})
    if n != expect_gpus:
        raise RuntimeError(f"expected {expect_gpus} visible GPUs, found {n}: {gpus} "
                           f"(set CUDA_VISIBLE_DEVICES in .env)")
    if expect_name and any(expect_name not in g["name"] for g in gpus):
        raise RuntimeError(f"expected every visible GPU to be {expect_name!r}: {gpus}")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 not supported on visible GPUs")
    # Peer access matters for splitting one model across both cards.
    p2p = torch.cuda.can_device_access_peer(0, 1) if n >= 2 else None
    return {"torch": torch.__version__, "torch_cuda": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(), "gpus": gpus, "peer_access_0_1": p2p}


@check("causal_conv1d_kernel")
def check_causal_conv1d():
    import torch
    from causal_conv1d.causal_conv1d_interface import causal_conv1d_fn, causal_conv1d_ref

    torch.manual_seed(0)
    b, d, l, w = 2, 256, 512, 4
    x = torch.randn(b, d, l, device="cuda", dtype=torch.bfloat16, requires_grad=True)
    wt = torch.randn(d, w, device="cuda", dtype=torch.float32, requires_grad=True)
    bias = torch.randn(d, device="cuda", dtype=torch.float32, requires_grad=True)
    out = causal_conv1d_fn(x, wt, bias, activation="silu")
    ref = causal_conv1d_ref(x.detach().float().requires_grad_(), wt, bias, activation="silu")
    fwd = max_err(out, ref)
    g = torch.randn_like(out)
    (gx,) = torch.autograd.grad(out, x, g)
    xr = x.detach().float().requires_grad_()
    (gxr,) = torch.autograd.grad(causal_conv1d_ref(xr, wt, bias, activation="silu"), xr, g.float())
    bwd = max_err(gx, gxr)
    if fwd > 5e-2 or bwd > 5e-2:
        raise RuntimeError(f"kernel disagrees with reference: fwd {fwd:.3g}, bwd {bwd:.3g}")
    return {"fwd_max_err": fwd, "bwd_max_err": bwd}


@check("mamba_selective_scan_kernel")
def check_selective_scan():
    import torch
    from mamba_ssm.ops.selective_scan_interface import selective_scan_fn, selective_scan_ref

    torch.manual_seed(0)
    b, d, n, l = 2, 64, 16, 256
    kw = dict(device="cuda", dtype=torch.float32)
    u, delta = torch.randn(b, d, l, **kw), torch.rand(b, d, l, **kw)
    A = -torch.rand(d, n, **kw)
    B, C = torch.randn(b, n, l, **kw), torch.randn(b, n, l, **kw)
    D = torch.randn(d, **kw)
    out = selective_scan_fn(u, delta, A, B, C, D, delta_softplus=True)
    ref = selective_scan_ref(u, delta, A, B, C, D, delta_softplus=True)
    err = max_err(out, ref)
    if err > 1e-2:
        raise RuntimeError(f"kernel disagrees with reference: {err:.3g}")
    return {"fwd_max_err": err}


@check("mamba2_ssd_kernel")
def check_ssd():
    """Mamba-2 chunked scan: the path both target models use."""
    import torch
    from mamba_ssm.ops.triton.ssd_combined import mamba_chunk_scan_combined, ssd_chunk_scan_combined_ref

    torch.manual_seed(0)
    b, l, h, p, g, n, chunk = 2, 512, 8, 64, 1, 64, 128
    kw = dict(device="cuda")
    x = torch.randn(b, l, h, p, dtype=torch.bfloat16, **kw)
    dt = torch.rand(b, l, h, dtype=torch.float32, **kw)
    A = -torch.rand(h, dtype=torch.float32, **kw)
    B = torch.randn(b, l, g, n, dtype=torch.bfloat16, **kw)
    C = torch.randn(b, l, g, n, dtype=torch.bfloat16, **kw)
    D = torch.randn(h, dtype=torch.float32, **kw)
    out = mamba_chunk_scan_combined(x, dt, A, B, C, chunk, D=D, dt_softplus=True)
    ref = ssd_chunk_scan_combined_ref(x.float(), dt, A, B.float(), C.float(), chunk, D=D, dt_softplus=True)
    err = max_err(out, ref)
    scale = ref.abs().max().item()
    if err > 2e-2 * max(scale, 1.0):
        raise RuntimeError(f"kernel disagrees with reference: {err:.3g} (ref scale {scale:.3g})")
    # One backward through the Triton path.
    xg = x.detach().requires_grad_()
    mamba_chunk_scan_combined(xg, dt, A, B, C, chunk, D=D, dt_softplus=True).float().sum().backward()
    if not torch.isfinite(xg.grad).all():
        raise RuntimeError("non-finite gradient from mamba_chunk_scan_combined backward")
    return {"fwd_max_err": err, "ref_scale": scale}


def _resolved_impl(fn):
    """Find the implementation a transformers kernel wrapper resolved to."""
    seen, stack = set(), [fn]
    while stack:
        f = stack.pop()
        if id(f) in seen or not callable(f):
            continue
        seen.add(id(f))
        cv = inspect.getclosurevars(f) if inspect.isfunction(f) else None
        if cv and "implementation" in cv.nonlocals:
            return cv.nonlocals["implementation"]
        if cv:
            stack.extend(v for v in cv.nonlocals.values() if inspect.isfunction(v))
        if hasattr(f, "__wrapped__"):
            stack.append(f.__wrapped__)
    return None


@check("transformers_fast_path")
def check_transformers_fast_path():
    """The model family must bind the installed kernels, not the PyTorch fallback.

    transformers decides at import time (use_kernel_func_from_hub_with_fallback): hub
    kernel, else the installed package, else a slow reference path. Every wrapped
    function in each modeling module must resolve to causal_conv1d or mamba_ssm.
    """
    required = {"causal_conv1d_fn", "causal_conv1d_update", "mamba2_chunk_scan",
                "mamba2_split_conv1d_scan_combined", "mamba2_selective_state_update"}
    result = {}
    for mod_name in ("nemotron_h",):
        mod = importlib.import_module(f"transformers.models.{mod_name}.modeling_{mod_name}")
        bound = {}
        for fname, fn in vars(mod).items():
            if not inspect.isfunction(fn) or fn.__module__ != mod.__name__:
                continue
            impl = _resolved_impl(fn)
            if impl is None:
                continue  # not a kernel wrapper
            origin = getattr(impl, "__module__", "") or ""
            if not origin.startswith(("causal_conv1d", "mamba_ssm")):
                raise RuntimeError(f"{mod_name}.{fname} falls back to {origin}, not a kernel package")
            bound[fname] = origin
        missing = required - bound.keys()
        if missing:
            raise RuntimeError(f"{mod_name}: kernel wrappers not found: {sorted(missing)}")
        result[mod_name] = bound
    return result


@check("xformers_kernel")
def check_xformers():
    """Unsloth silently drops to PyTorch attention when xformers' extensions fail to load."""
    import torch
    import xformers
    import xformers.ops as xops

    torch.manual_seed(0)
    b, l, h, d = 2, 256, 8, 64
    q, k, v = (torch.randn(b, l, h, d, device="cuda", dtype=torch.bfloat16) for _ in range(3))
    out = xops.memory_efficient_attention(q, k, v, attn_bias=xops.LowerTriangularMask())
    ref = torch.nn.functional.scaled_dot_product_attention(
        q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2), is_causal=True).transpose(1, 2)
    err = max_err(out, ref)
    if err > 3e-2:
        raise RuntimeError(f"kernel disagrees with reference: {err:.3g}")
    return {"xformers": xformers.__version__, "fwd_max_err": err}


UNSLOTH_PROBE = """
import json
import unsloth  # first, as the trainer imports it: the import patches transformers
from unsloth.models import _utils as uu
print(json.dumps({"xformers_attention": uu.xformers_attention is not None}))
"""


@check("unsloth")
def check_unsloth():
    """Import Unsloth in a fresh interpreter, before anything else, as the trainer does.
    In this process transformers is already imported by the earlier checks, and Unsloth
    patches at import, so its result here could differ from the trainer's."""
    proc = subprocess.run([sys.executable, "-c", UNSLOTH_PROBE], capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"import unsloth failed: {proc.stderr.strip()[-2000:]}")
    probe = json.loads(proc.stdout.strip().splitlines()[-1])
    # Unsloth decides at import whether xformers works and sets
    # unsloth.models._utils.xformers_attention to None when it falls back.
    if not probe["xformers_attention"]:
        raise RuntimeError("Unsloth disabled xformers at import (see UNSLOTH_ENABLE_LOGGING=1)")
    return {"unsloth": md.version("unsloth"), "unsloth_zoo": md.version("unsloth_zoo")}


def versions():
    pkgs = ["torch", "torchvision", "triton", "transformers", "trl", "peft", "accelerate",
            "datasets", "unsloth", "unsloth_zoo", "mamba_ssm", "causal_conv1d", "xformers",
            "bitsandbytes", "huggingface_hub", "safetensors"]
    out = {}
    for p in pkgs:
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", help="write the JSON report here instead of stdout")
    ap.add_argument("--expect-gpus", type=int, default=int(os.environ.get("WEAVER_TRAIN_EXPECT_GPUS", "2")))
    ap.add_argument("--expect-gpu-name", default=os.environ.get("WEAVER_TRAIN_EXPECT_GPU_NAME", "RTX A6000"))
    args = ap.parse_args(argv)

    ok = True
    try:
        check_driver()
        check_torch_cuda(args.expect_gpus, args.expect_gpu_name or None)
        # Unsloth's import rewrites a mamba-ssm kernel file (config/known-modified-files.toml);
        # probe it first, so the kernel checks below run the file training will use.
        check_unsloth()
        check_causal_conv1d()
        check_selective_scan()
        check_ssd()
        check_transformers_fast_path()
        check_xformers()
    except CheckFailed:
        ok = False

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": ok,
        "python": platform.python_version(),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "packages": versions(),
        "checks": CHECKS,
    }
    text = json.dumps(report, indent=2, default=str)
    if args.report:
        with open(args.report, "w") as f:
            f.write(text + "\n")
    else:
        print(text)
    for c in CHECKS:
        print(f"[{'ok' if c['ok'] else 'FAIL'}] {c['check']}" + ("" if c["ok"] else f": {c['detail']}"),
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
