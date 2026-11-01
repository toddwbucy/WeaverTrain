#!/usr/bin/env bash
# Build and verify the weaver-train environment. Safe to run any number of times:
# every stage checks whether its work is already done and skips it if so.
#
#   scripts/setup-env.sh                 all stages
#   scripts/setup-env.sh --only verify   one stage (preflight always runs)
#
# Stages, in order (each stops the script on failure):
#   preflight  tools present, .env loaded, driver new enough, expected GPUs visible
#   env        uv sync --locked from uv.lock (skipped when the venv already matches)
#   lock       environment.lock: the packages actually installed. Written on first run;
#              afterwards the installed set must match it exactly (--update-lock to
#              accept a deliberate change)
#   verify     scripts/verify_env.py: run each CUDA kernel against its reference, check
#              transformers binds the kernels for both model families, import Unsloth.
#              Skipped when it already passed for this exact lock + driver (--reverify)
#   models     scripts/fetch_artifacts.py --set models: download pinned revisions, check
#              against the publisher's digests and manifests/models/*.json, stage the
#              fast-load copy. Skipped per model when verified and unchanged (--rehash)
#   benchmarks scripts/fetch_artifacts.py --set benchmarks, then scripts/build_snapshot.py:
#              archive pinned benchmark sources, freeze the dated snapshots in
#              config/snapshots.toml (a rebuild must reproduce the committed hash)
#   datasets   scripts/fetch_artifacts.py --set datasets: candidate training data
#              (config/datasets.toml) and the whole-store manifest.
#   tests      the unit suite (tests/, stand-in model, GPU tests included). Skipped when it
#              passed before for this lock + driver + source tree (--retest)
#
# Configuration comes from .env (copy .env.example). See docs/environment.md.

set -Eeuo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Minimum driver: the torch wheels are built for CUDA 13.0.
MIN_DRIVER_CUDA_MAJOR=13
STAGES=(preflight env lock verify models benchmarks datasets tests)

only=""
reverify=0
rehash=0
update_lock=0
retest=0
skip_cache=0

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; cat <<'EOF'
Options:
  --only STAGE     run preflight and then only STAGE (env|lock|verify|models|benchmarks|datasets|tests)
  --reverify       re-run kernel verification even if it passed before
  --rehash         full sha256 check of model trees, ignoring stamps
  --retest         re-run the unit suite even if it passed before
  --update-lock    rewrite environment.lock from the installed venv
  --skip-cache     do not stage the fast-load model copy
  -h, --help       this text
EOF
}

while (($#)); do
  case "$1" in
    --only) only="${2:?--only needs a stage}"; shift 2 ;;
    --reverify) reverify=1; shift ;;
    --rehash) rehash=1; shift ;;
    --update-lock) update_lock=1; shift ;;
    --retest) retest=1; shift ;;
    --skip-cache) skip_cache=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ -n "$only" && ! " ${STAGES[*]} " == *" $only "* ]]; then
  echo "unknown stage: $only (one of: ${STAGES[*]})" >&2; exit 2
fi

say()  { printf '\n== %s\n' "$*"; }
skip() { printf '   skip: %s\n' "$*"; }
die()  { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }
want() { [[ -z "$only" || "$only" == "$1" ]]; }

trap 'die "line $LINENO: $BASH_COMMAND"' ERR

# --------------------------------------------------------------------- preflight
say "preflight"
for tool in uv nvidia-smi rsync git; do
  command -v "$tool" >/dev/null || die "$tool not found on PATH"
done
[[ -f .env ]] || die ".env missing: cp .env.example .env and fill it in"
set -a; . ./.env; set +a
: "${WEAVER_TRAIN_STATE_DIR:=./var}"
[[ "$WEAVER_TRAIN_STATE_DIR" = /* ]] || WEAVER_TRAIN_STATE_DIR="$REPO/$WEAVER_TRAIN_STATE_DIR"
export WEAVER_TRAIN_STATE_DIR
mkdir -p "$WEAVER_TRAIN_STATE_DIR/stamps"
[[ "${CUDA_DEVICE_ORDER:-}" == PCI_BUS_ID ]] \
  || die "CUDA_DEVICE_ORDER must be PCI_BUS_ID in .env (default order is a heuristic and can reshuffle GPUs)"
[[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || die "CUDA_VISIBLE_DEVICES must be set in .env"

driver_cuda="$(nvidia-smi | sed -n 's/.*CUDA [A-Za-z ]*Version: *\([0-9]*\)\..*/\1/p' | head -1)"
driver_ver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
[[ -n "$driver_cuda" ]] || die "could not read the driver's CUDA version from nvidia-smi"
((driver_cuda >= MIN_DRIVER_CUDA_MAJOR)) \
  || die "driver supports CUDA $driver_cuda; need >= $MIN_DRIVER_CUDA_MAJOR for the cu130 torch wheels"
echo "   driver $driver_ver (CUDA $driver_cuda), GPUs $CUDA_VISIBLE_DEVICES, state $WEAVER_TRAIN_STATE_DIR"

# The venv lives in the repo; copying from uv's cache (rather than hardlinking) keeps
# it independent of whichever filesystem holds UV_CACHE_DIR.
export UV_LINK_MODE=copy
PY="$REPO/.venv/bin/python"

# --------------------------------------------------------------------------- env
if want env; then
  say "env (uv.lock -> .venv)"
  # Not 'uv sync --check': the mamba-ssm / causal-conv1d wheels carry a local version
  # in their filename that their metadata lacks, so uv always reports them outdated.
  # Instead: skip when uv.lock is unchanged since the last successful sync; the lock
  # stage then catches any drift in what is actually installed.
  env_key="$(cat uv.lock pyproject.toml | sha256sum | cut -d' ' -f1)"
  env_stamp="$WEAVER_TRAIN_STATE_DIR/stamps/env-sync.ok"
  if [[ -x "$PY" && -f "$env_stamp" && "$(cat "$env_stamp")" == "$env_key" ]]; then
    skip ".venv synced from this uv.lock already"
  else
    rm -f "$env_stamp"
    uv sync --locked
    echo "$env_key" > "$env_stamp"
  fi
fi

# -------------------------------------------------------------------------- lock
freeze() { "$PY" scripts/freeze_env.py; }

if want lock; then
  say "lock (environment.lock)"
  [[ -x "$PY" ]] || die ".venv missing; run the env stage first"
  current="$(freeze)"
  if [[ ! -f environment.lock || $update_lock -eq 1 ]]; then
    printf '%s\n' "$current" > environment.lock
    echo "   wrote environment.lock ($(grep -vc '^#' environment.lock) packages)"
  elif diff -u environment.lock <(printf '%s\n' "$current") >/dev/null; then
    skip "installed packages match environment.lock"
  else
    diff -u environment.lock <(printf '%s\n' "$current") >&2 || true
    die "installed packages drifted from environment.lock (above). Repair with 'uv sync --locked --reinstall', or accept a deliberate change with --update-lock"
  fi
  # The lock reads RECORD, which keeps the install-time hashes; this hashes the files.
  "$PY" scripts/freeze_env.py --check-files training \
    || die "installed files differ from their RECORD hashes (above) and config/known-modified-files.toml does not list them. Repair with 'uv sync --locked --reinstall-package <name>'"
fi

# ------------------------------------------------------------------------ verify
if want verify; then
  say "verify (kernels, fast path, Unsloth)"
  [[ -f environment.lock ]] || die "environment.lock missing; run the lock stage first"
  # The key sees only the listed modified files; any other change to an installed file
  # must fail here, even when the lock stage did not run (--only verify).
  if [[ -n "$only" ]]; then
    "$PY" scripts/freeze_env.py --check-files training \
      || die "installed files differ from their RECORD hashes (above) and config/known-modified-files.toml does not list them"
  fi
  # The key covers the content of the files config/known-modified-files.toml lists, which
  # environment.lock (RECORD) cannot see; it is taken again after the run, because the
  # Unsloth probe rewrites one of them on a fresh install.
  verify_key() {
    { cat environment.lock
      echo "driver=$driver_ver gpus=$CUDA_VISIBLE_DEVICES expect=${WEAVER_TRAIN_EXPECT_GPUS:-} name=${WEAVER_TRAIN_EXPECT_GPU_NAME:-}"
      sha256sum scripts/verify_env.py config/known-modified-files.toml
      "$PY" scripts/freeze_env.py --known-state training; } | sha256sum | cut -d' ' -f1
  }
  key="$(verify_key)"
  stamp="$WEAVER_TRAIN_STATE_DIR/stamps/verify-env.ok"
  report="$WEAVER_TRAIN_STATE_DIR/env-report.json"
  if [[ $reverify -eq 0 && -f "$stamp" && "$(cat "$stamp")" == "$key" ]]; then
    skip "passed before for this lock + driver ($report)"
  else
    rm -f "$stamp"
    "$PY" scripts/verify_env.py --report "$report" 2> >(grep -E '^\[(ok|FAIL)\]' >&2) \
      || die "environment verification failed; details in $report"
    verify_key > "$stamp"
    echo "   report: $report"
  fi
fi

# ------------------------------------------------------------------------ models
if want models; then
  say "models (config/models.toml)"
  args=()
  ((rehash)) && args+=(--rehash)
  ((skip_cache)) && args+=(--skip-cache)
  "$PY" scripts/fetch_artifacts.py --set models "${args[@]}" || die "model fetch/verify failed"
fi

# -------------------------------------------------------------------- benchmarks
if want benchmarks; then
  say "benchmarks (config/benchmarks.toml, config/snapshots.toml)"
  args=()
  ((rehash)) && args+=(--rehash)
  "$PY" scripts/fetch_artifacts.py --set benchmarks "${args[@]}" || die "benchmark source fetch/verify failed"
  "$PY" scripts/build_snapshot.py "${args[@]}" || die "snapshot build/verify failed"
fi

# ---------------------------------------------------------------------- datasets
if want datasets; then
  say "datasets (config/datasets.toml)"
  args=()
  ((rehash)) && args+=(--rehash)
  "$PY" scripts/fetch_artifacts.py --set datasets "${args[@]}" || die "dataset fetch/verify failed"
fi

# ------------------------------------------------------------------------- tests
if want tests; then
  say "tests (unit suite on the stand-in model)"
  src_key="$( { cat environment.lock; echo "driver=$driver_ver gpus=$CUDA_VISIBLE_DEVICES";
               find src tests scripts pyproject.toml -type f \( -name '*.py' -o -name '*.toml' \) -print0 \
                 | LC_ALL=C sort -z | xargs -0 sha256sum; } | sha256sum | cut -d' ' -f1)"
  stamp="$WEAVER_TRAIN_STATE_DIR/stamps/tests.ok"
  if [[ $retest -eq 0 && -f "$stamp" && "$(cat "$stamp")" == "$src_key" ]]; then
    skip "suite passed before for this lock + driver + source"
  else
    rm -f "$stamp"
    "$PY" -m pytest -q -p no:warnings 2>&1 | tail -3
    [[ ${PIPESTATUS[0]} -eq 0 ]] || die "unit tests failed"
    echo "$src_key" > "$stamp"
  fi
fi

say "done"
