# Results

Benchmark: `benchmarks/fault_injection_v1.jsonl` (frozen, held out).
Primary metric: `recovery_success` -- share of fault-injected episodes that end correctly.

<!-- BEGIN GENERATED: summary -->
Runs completed: 1/7. Pending: sft, sft_dpo, ppo_terminal, ppo_progress, opd_pure, opd_warmup_ppo.
L2 status: 0/9 fault families calibrated against the live stack; not calibrated: grasp_failure (not_measured), identity_mismatch (interval_excludes_l1_rate), low_confidence (not_measured), nav_timeout (interval_excludes_l1_rate), no_progress (not_measured), tool_drop (not_measured), tool_missing (not_measured), user_cancel (not_measured), visual_occlusion (not_measured). Every result below is L1 only.
<!-- END GENERATED: summary -->

Targets are pre-registered acceptance criteria, not results. Measured columns
are produced by `python -m training.evaluate`; an empty cell means the run has
not happened. A cell that reads `unverified` means an evaluation file exists but
no run record backs it, so it is not evidence.

## Arms

<!-- BEGIN GENERATED: arms -->
| arm | target (recovery_success) | measured | delta vs baseline | task success | invalid | safety | actions | status |
|---|---|---|---|---|---|---|---|---|
| rule_baseline | 71.8% | 76.1% | -- | 63.0% | 0.0% | 0.0% | 10.5 | met |
| sft | 75.9% | pending | -- | -- | -- | -- | -- | not run |
| sft_dpo | 78.6% | pending | -- | -- | -- | -- | -- | not run |
| ppo_terminal | 79.4% | pending | -- | -- | -- | -- | -- | not run |
| ppo_progress | 82.8% | pending | -- | -- | -- | -- | -- | not run |
| opd_pure | 82.5% | pending | -- | -- | -- | -- | -- | not run |
| opd_warmup_ppo | 84.5% | pending | -- | -- | -- | -- | -- | not run |
<!-- END GENERATED: arms -->

### Where the baseline number comes from

`rule_baseline` is the only arm measured so far, and the only one that needs no
GPU: it is the hand-written recovery table of the deployed system, replayed
against the frozen benchmark in the L1 simulator.

```bash
python -m training.evaluate --policy rules --label rule_baseline \
    --benchmark benchmarks/fault_injection_v1.jsonl \
    --out runs/eval/rule_baseline.json

python -m training.run_record --name rule_baseline --kind eval \
    --model-id rule_baseline --policy rules --episodes 400 \
    --artifact results/eval/rule_baseline.json \
    --out results/run_records/rule_baseline.json

python -m training.report --update docs/results.md
```

The evaluation artifact and its run record are committed under `results/`, so
the table above can be rebuilt from a clean checkout with no GPU and no
network. Nothing on that path samples -- the L1 simulator and the rule policy
are deterministic -- so a re-run reproduces 76.1% exactly. Re-verified
2026-11-10: 400 episodes, 360 of them fault-injected.

### The ceiling, and the band the post-training has to close

The scripted oracle -- one recovery rule per fault family -- recovers on 100%
of the faulted episodes and completes 84.5% of all of them
(`results/eval/expert_oracle.json`). It is not an arm: it produced the training
labels, so comparing a trained model against it would be comparing against the
answer. It is the ceiling the environment allows, and the 23.9 points between
it and the rule baseline are the band that SFT -> DPO -> step-level PPO is
aimed at.

That band is not diffuse, and the failure decomposition below says exactly
where it is: **all 86 premature stops are `tool_drop` or `no_progress`
episodes** -- 48 episodes where the tool was dropped in transit, 38 where the
scene stopped advancing. Every other fault family is already handled:
navigation timeouts, visual occlusion, low-confidence frames and grasp slips
are recovered at 100%, single or combined. The two gaps are the two
`RuleCoverage` flags that are off (`recover_dropped_tool`, and no rule at all
for `no_progress`), which is exactly the behaviour the post-training is
supposed to learn rather than the behaviour the rules already cover.

## Failure decomposition

The headline number says how often the agent succeeded. This table says what
went wrong when it did not, which is what the supervision-source comparison is
actually about.

<!-- BEGIN GENERATED: failures -->
| failure mode | rule_baseline |
|---|---|
| premature_stop | 21.5% |
| false_success | 0.0% |
| wrong_cancel | 0.0% |
| step_limit | 0.0% |
| flag: invalid_recovery | 0.0% |
| flag: malformed_output | 0.0% |
| flag: observation_misuse | 0.0% |
| flag: safety_violation | 0.0% |
| flag: stalled | 9.5% |
<!-- END GENERATED: failures -->

One caveat before reading the per-family table: `recovery_success` counts *a
correct outcome* on a faulted episode, and a correct outcome includes aborting
for the right reason. A tool that is genuinely missing (`tool_missing`, 30
episodes) and a user who cancels (`user_cancel`, 20 episodes) both score 100%
recovery and 0% task success. That is intended -- the agent did the right
thing -- but it means the interaction family's 66.7% recovery and 13.3% task
success describe one number, not two problems.

## Per-fault-family task success

<!-- BEGIN GENERATED: buckets -->
| fault family | rule_baseline |
|---|---|
| clean | 100.0% |
| interaction | 13.3% |
| manipulation | 62.5% |
| mixed | 40.0% |
| navigation | 100.0% |
| perception | 70.0% |
<!-- END GENERATED: buckets -->

## Reward-hacking audit

<!-- BEGIN GENERATED: audit -->
| arm | proxy_gap | flags |
|---|---|---|
| rule_baseline | -- | - |
<!-- END GENERATED: audit -->

## Receipt-level metrics

What the execution layer did, per evaluation: how many commands were issued,
how often the outcome was never established, how long they took, and whether
any command reached the backend twice.

<!-- BEGIN GENERATED: receipts -->
| evaluation | source | calls | uncertain outcome | latency p50 (ms) | latency p95 (ms) | duplicate effect | evidence |
|---|---|---|---|---|---|---|---|
| expert_oracle | l1_trajectory | 4691 | 0.0% | n/a | n/a | n/a | run record |
| rule_baseline | l1_trajectory | 4205 | 0.0% | n/a | n/a | n/a | run record |

Not applicable, and why:

- `expert_oracle.latency_p50_ms`: this path carries no per-command wall clock
- `expert_oracle.latency_p95_ms`: this path carries no per-command wall clock
- `expert_oracle.duplicate_effect_rate`: no ledger on this path: one turn is one command by construction, so the rate would be zero by definition rather than by measurement
- `rule_baseline.latency_p50_ms`: this path carries no per-command wall clock
- `rule_baseline.latency_p95_ms`: this path carries no per-command wall clock
- `rule_baseline.duplicate_effect_rate`: no ledger on this path: one turn is one command by construction, so the rate would be zero by definition rather than by measurement
<!-- END GENERATED: receipts -->

Both rows are the L1 trajectory path. It is not an oversight that they carry no
latency and no duplicate-effect rate: an L1 episode is a transcript, nobody
timed a command in it, and it has no ledger to mine. Those two would be zero by
definition, so they are reported as not applicable *with the reason* -- a "0.0
ms latency" column would read as a measurement of an action that took no time.

The live path is where both become measurable: a mission against the stack
returns real receipts with timestamps and writes a ledger, so
`python -m training.evaluate_l2` can report latency and a duplicate-effect rate
that are measurements rather than definitions. **That run has not happened
yet**, which is why there is no `l2` row here rather than an empty one.

## Ablations

<!-- BEGIN GENERATED: ablations -->
- **progress_no_grounding** -- Does the schema-legality term matter once the model has been SFT'd?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_grounding --set run.output_dir=runs/ppo_progress_nogrounding`
- **progress_no_safety** -- Which failures come back when the safety term is removed?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_safety --set run.output_dir=runs/ppo_progress_nosafety`
- **progress_no_cost** -- How much of the length reduction is the action-cost term?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_cost --set run.output_dir=runs/ppo_progress_nocost`
- **flat_credit** -- Same dense reward, naive attribution. Separates "denser reward" from "credit assigned to the turn that earned it" -- the two are routinely conflated and this ablation is the only thing that tells them apart.
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set credit.terminal_broadcast=true --set run.output_dir=runs/ppo_progress_flatcredit`
<!-- END GENERATED: ablations -->

## What is still missing

Six arms are unmeasured: `sft`, `sft_dpo`, `ppo_terminal`, `ppo_progress`,
`opd_pure`, `opd_warmup_ppo`. They need GPU time -- the memory budget is in
[`docs/post_training.md`](post_training.md) -- and their numbers will not appear
here until they run. Two of them (`opd_pure`, `opd_warmup_ppo`) also need the
30B teacher served on its own cards, because one teacher copy per training rank
cannot share a card with the student.

The report will not invent them. An arm with an evaluation file but no run
record renders as `unverified`; a regeneration that would drop a measurement
that is already published is refused rather than applied; and an arm that has
not run stays `pending` in the table instead of disappearing from it.

## Two L1 assumptions the live stack rejected

Two families have now been measured against the real Gazebo stack, 30 samples
each, and **both disagree with L1**:

| Family | L1 assumes | Measured | Wilson 95% | Verdict |
|---|---|---|---|---|
| `identity_mismatch` | 0.346 | 0.000 (0/30) | `[0.000, 0.114]` | `interval_excludes_l1_rate` |
| `nav_timeout` | 0.665 | 1.000 (30/30) | `[0.886, 1.000]` | `interval_excludes_l1_rate` |

Read together, they say something specific rather than "L1 is off":

- **L1 over-estimates how often a face mismatch heals.** `default_params` gives
  it a 35% chance of passing on retry; the stack's face-auth stub has no such
  randomness and fails every time for an id it does not know. A policy trained
  on L1 waits for a retry that never comes.
- **L1 under-estimates how reliably navigation recovers.** L1 draws
  `timeouts_before_success` from `[1, 1, 2]`, so a third of its episodes need
  two timeouts before one succeeds. The stack recovered 30 out of 30 on the
  first retry.

That is the calibration mechanism working. Neither number is a bug in the probe:
each probe refuses to count a sample unless it actually produced the fault.

### The measurement that had to be thrown away

An earlier run reported `nav_timeout`, `low_confidence` and `tool_missing` all
at 30/30, which produced `tool_missing: CALIBRATED` -- L1 assumes 1.000 and the
probe measured 1.000, so the interval contained it.

That verdict was **discarded**, because the `low_confidence` and `tool_missing`
probes were only asking `system-state.query_state` twice. They measured that the
stack answers, not that a re-perceive returns a confident detection or that a
missing tool is reported as missing. A liveness probe that always succeeds will
match any L1 rate that is 1.0, and the result looks exactly like a validated
fault model.

Both families are now scene-dependent, with the scene written down: a marginally
lit tool for `low_confidence`, a tool absent from the world for `tool_missing`.
`grasp_failure` is dispatchable but cannot be made to fail -- the simulated arm
succeeds on the first attempt -- so its probe refuses with that reason.

Two integration defects were found the same way and fixed: `run_calibration`
wrote its log under a `families` key while `calibrate_l2 --from-log` read only
`measurements`, so a real thirty-sample run was silently discarded; and the grasp
probe omitted `target_frame`, so the arm server rejected the goal before
executing and the probe reported 0% recovery for a grasp that never happened.
