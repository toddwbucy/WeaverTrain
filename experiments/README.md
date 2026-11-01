# Experiments

Local work trees, one directory per experiment. Only this README is committed: the
trees hold text derived from the operator's copy of ASD-STE100, dataset text and model
outputs. Results that matter are written up as reports; scripts live in `scripts/`.

## ste-rewrite: which model rewrites training data into STE100

Every model gets the same 60 segments, the same rules and the same rounds, each in its
own directory and its own opencode session, so several models can run at once across
providers. `scripts/ste_experiment.py` keeps the bookkeeping.

```bash
.venv/bin/python scripts/ste_experiment.py kit                    # once: build kit/
.venv/bin/python scripts/ste_experiment.py new deepseek-v4.1-flash glm-5.3-flash
# in each experiments/ste-rewrite/<model>/: start opencode with that model and paste
#   Read MONITOR.md and do exactly what it says.
# the session now watches <model>/messages/ and takes each new file as its next task
.venv/bin/python scripts/ste_experiment.py send round1.md         # same message to every model
.venv/bin/python scripts/ste_experiment.py status                 # done or pending, rounds
.venv/bin/python scripts/ste_experiment.py repair                 # checker feedback for every model
.venv/bin/python scripts/ste_experiment.py send repair.md         # "Read REPAIR-PROMPT.md and do ..."
.venv/bin/python scripts/ste_experiment.py score                  # SCORES.md, <model>/score.md
```

Messages are numbered (`001-round1.md`, `002-repair.md`); a model writes
`NNN-<name>.done` with a short report when it finishes one. `send` refuses while any
model still has a message pending (`--force` queues anyway). A message that holds only
STOP ends a session's watch loop.

Round files: `round1.jsonl` (first pass), `repair-1.jsonl` (failures with the checker's
notes), `round2.jsonl` (corrections), and so on. A model never runs the checker;
`repair` does it for all models in the same round. A model directory refuses to score
if its kit differs from `kit/`. Runs made before this layout are in `_earlier/`.
