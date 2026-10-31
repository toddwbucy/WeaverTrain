# Benchmarks

How weaver-train's evaluation sets are chosen, frozen and verified, and what is still
open. Established 2026-10-31.

## Contamination floor

Tasks must be created after both models' training cutoffs, so a model cannot have
seen the fix. From the model cards (read 2026-10-31):

| Model | Cutoff |
|---|---|
| granite-4.0-h-small | released 2025-10-02; no cutoff stated, so it is no later than that |
| nemotron-3-nano-30b-a3b | pre-training 2025-06-25, post-training 2025-11-28 |

Floor: **created on or after 2025-12-01**.

## Pipeline

```text
config/benchmarks.toml --fetch_artifacts.py--> sources (pinned revision, upstream digests,
                                                read-only, manifests/sources/)
config/snapshots.toml  --build_snapshot.py----> snapshot dir: tasks.jsonl + SNAPSHOT.json
                                                (read-only, manifests/snapshots/)
weaver_train.bench     -- verifies the snapshot tree hash, then runs a runner per task
```

A snapshot is deterministic: tasks are sorted by `task_id` and serialized with sorted
keys. A rebuild from the pinned source must reproduce the committed tree hash, and
`build_snapshot.py` refuses if it does not. A different selection is a new snapshot
with a new name, never an edit. `SNAPSHOT.json` records the source repo, revision and
source tree hash, the filter, the counts and any caveats. The snapshot's tree hash is
what run records cite.

## Frozen snapshots (2026-10-31)

| Snapshot | Tasks | Created | Language | Caveat |
|---|---|---|---|---|
| `swe-rebench-post-cutoff-2026-10-31` | 263 from 172 repos | 2025-12-01 .. 2026-05-12 | Python | none: all post-cutoff |
| `multi-swe-bench-rust-2026-10-31` | 239 from 10 repos | not recorded (dataset published 2025-04) | Rust | **all pre-cutoff; contamination not excluded; delta-only** |

**Reading the Rust snapshot: delta-only** (operator's ruling, 2026-10-31). Its absolute
scores are not evidence of capability and are never reported alone. Only the
difference between trained and untrained runs of the same model is read, because both
runs share the same contamination. Comparing the two models' absolute Rust scores is
not a valid use of this snapshot. The snapshot itself is unchanged; this ruling governs
how its results are reported.

## Findings

- **No post-cutoff Rust set with runnable environments was found.** Multi-SWE-bench
  Rust has no date field and predates both cutoffs. SWE-rebench-V2 has 3,123 Rust tasks
  but ends 2025-07 (the whole set ends 2025-10-31). SWE-rebench-V2-PRs has dates but no
  environments (pull requests only). The Rust snapshot is frozen because the brief
  names it; its scores cannot be read as uncontaminated.
- **SWE-rebench has moved its newest tasks to Harbor Hub.** The Hugging Face leaderboard
  dataset (revision `34d5a588`, updated 2026-07-28) ends with split 2026_03. Its README
  points to Harbor Hub for the July 2026 split. More post-cutoff tasks exist there,
  possibly in more languages, but using them adds a dependency (the Harbor framework).
- **Image storage is large.** SWE-rebench images are prebuilt on Docker Hub. For the 181
  of 263 snapshot images whose size Docker Hub returned before rate-limiting, the
  compressed total is 334 GB (median 1.45 GB, largest 7.5 GB). Expect about 480 GB
  compressed for all 263, and more once unpacked. Shared base layers reduce this, but
  holding every image at once needs over 1 TB under the Docker root. The alternative is
  pull, run, remove, task by task.

## Open decisions (before the harness)

1. **Agent scaffold.** A task is solved by an agent loop (the model plus shell tools)
   that produces a patch, which the harness then tests. The scaffold strongly affects
   scores and must be fixed across untrained and trained runs and both models.
2. **Harness.** SWE-rebench: Nebius's SWE-bench fork, or Harbor. Multi-SWE-bench: its
   own harness. Either becomes a `weaver_train.bench` runner, probably in its own
   environment, so its dependencies do not enter the training lock.
3. **Docker storage**: where the Docker root lives after the fastpool changeover, and
   whether images are kept or pulled per task.
4. **Rust**: accept the pre-cutoff Multi-SWE-bench set with its caveat, pull post-cutoff
   tasks from Harbor Hub, or build post-cutoff Rust tasks.
