# Results

Benchmark: `benchmarks/fault_injection_v1.jsonl` (frozen, held out).
Primary metric: `recovery_success` -- share of fault-injected episodes that end correctly.

Runs completed: 1/7. Pending: sft, sft_dpo, ppo_terminal, ppo_progress, opd_pure, opd_warmup_ppo.

Targets are pre-registered acceptance criteria, not results. Measured columns
are produced by `python -m training.evaluate`; an empty cell means the run has
not happened.

## Arms

| arm | target (recovery_success) | measured | task success | invalid | safety | actions | status |
|---|---|---|---|---|---|---|---|
| rule_baseline | 71.8% | 76.1% | 63.0% | 0.0% | 0.0% | 10.5 | met |
| sft | 75.9% | pending | -- | -- | -- | -- | not run |
| sft_dpo | 78.6% | pending | -- | -- | -- | -- | not run |
| ppo_terminal | 79.4% | pending | -- | -- | -- | -- | not run |
| ppo_progress | 82.8% | pending | -- | -- | -- | -- | not run |
| opd_pure | 82.5% | pending | -- | -- | -- | -- | not run |
| opd_warmup_ppo | 84.5% | pending | -- | -- | -- | -- | not run |

## Failure decomposition

The headline number says how often the agent succeeded. This table says what
went wrong when it did not, which is what the supervision-source comparison is
actually about.

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

## Per-fault-family task success

| fault family | rule_baseline |
|---|---|
| clean | 100.0% |
| interaction | 13.3% |
| manipulation | 62.5% |
| mixed | 40.0% |
| navigation | 100.0% |
| perception | 70.0% |

## Ablations

- **progress_no_grounding** -- Does the schema-legality term matter once the model has been SFT'd?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_grounding --set run.output_dir=runs/ppo_progress_nogrounding`
- **progress_no_safety** -- Which failures come back when the safety term is removed?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_safety --set run.output_dir=runs/ppo_progress_nosafety`
- **progress_no_cost** -- How much of the length reduction is the action-cost term?
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set reward.preset=progress_no_cost --set run.output_dir=runs/ppo_progress_nocost`
- **flat_credit** -- Same dense reward, naive attribution. Separates "denser reward" from "credit assigned to the turn that earned it" -- the two are routinely conflated and this ablation is the only thing that tells them apart.
  `python -m training.train_ppo --config training/configs/ppo_progress.yaml --set credit.terminal_broadcast=true --set run.output_dir=runs/ppo_progress_flatcredit`
