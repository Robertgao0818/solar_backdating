# AGENT.md (compat stub)

This file is kept for backward compatibility with tooling that still looks
for `AGENT.md`. The canonical agent guidance is in
[`AGENTS.md`](AGENTS.md).

This repo is the V1.4 install-date sub-line of ZAsolar. It is **not** the
old `geid_bbox` GEID bbox detection prototype — that has been archived
under `/home/gao/projects/_archive/geid_bbox_legacy_2026-05-05/`.

## ISSUE-29 long-running execution (2026-07-23)

The owner has explicitly released ISSUE-29's owner-sequencing hold.  Run the
CoJ 2019/2023 external-robustness work as a resumable detached tmux job; do not
keep a foreground agent/tool call open for the bounded pilot.

- Canonical session: `issue29_coj_robustness`.
- Canonical launcher: `scripts/validation/run_issue29_coj_robustness.sh`.
- Output root:
  `~/zasolar_data/geid_temporal/coj_run3_external_robustness_2026-07/`.
- The launcher must activate `scripts/activate_env.sh`, use the local Sub2API
  gateway with `gemini-3.1-flash-lite`, append unbuffered output to
  `run.log`, and write per-stage `.done` sentinels only after validation.
- Re-entry is resume-only: never delete locks, samples, observations, audit
  sidecars, or completed sentinels merely to restart a failed process.
- Before launch, refuse a duplicate live tmux session and run an authenticated
  one-image gateway preflight.  A failed preflight must stop before scoring.
- Observe with `tmux capture-pane -pt issue29_coj_robustness:0 -S -80` and the
  persisted log; do not repeatedly attach/detach from automation.
- CoJ results remain isolated from DINO R4/R5 model selection.  Gemini output
  is an automated observation, not blind-human adjudication; without the
  preregistered human gate, reporting must stop at `PENDING_HUMAN_GATE` and
  must not emit a partial robustness verdict.

Launch and monitor:

```bash
bash scripts/validation/run_issue29_coj_robustness.sh launch
bash scripts/validation/run_issue29_coj_robustness.sh status
tmux capture-pane -pt issue29_coj_robustness:0 -S -80
```

Owner-authorized Stage-D full expansion uses session `issue29_coj_full` and
`scripts/validation/run_issue29_coj_full_expansion.sh`, with 500-target atomic
Parquet shards. The frozen production settings are 60 workers and QPS 8.
