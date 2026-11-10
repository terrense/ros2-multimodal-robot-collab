# training — post-training stack for the delivery agent

The high-level policy is an LLM that emits one skill call per turn. This
directory turns that into something trainable: a chat surface, a reward, a credit
assignment scheme, four training stages and an evaluation that can say *what*
went wrong rather than only how often.

This is the **PT** line (post-training). The environment lives in
[`agent_env/`](../agent_env/README.md); the runtime that executes the skill calls
lives in [`agent_harness/`](../agent_harness/). Line prefixes and the full map:
[`docs/specs/README.md`](../docs/specs/README.md).

## What problem this is for

The deployed system — LLM planner on top of a ROS2 state machine — already
handles the structured workstation. It degrades when the environment misbehaves:
a Nav2 timeout, an occluded tool, a grasp that slips, a payload dropped in
transit, an operator who is not at the station. Each of those has a hand-written
recovery rule; combinations of them do not, and the rule tree grows faster than
the coverage does.

So the target is not motor control. It is one question:

> when a high-level action did not produce its expected effect, what should the
> agent do next?

`RuleBaselinePolicy` makes the gap concrete: with recovery rules for the
anticipated single faults and none for the rest, it recovers on **76.1%** of the
400 held-out fault-injected episodes, against **100%** for the scripted oracle
that has a rule for every family. That 24-point band is what post-training is
aiming at, and it is not diffuse: all 86 of its premature stops are `tool_drop`
or `no_progress` episodes — the two recoveries the deployed system does not
have. See [`docs/results.md`](../docs/results.md).

## Layout

| Module | Role |
|---|---|
| `chat_format.py` | The only prompt surface. Observation → messages, text → `Action`, with forgiving *format* parsing and strict *content* validation. |
| `tokenization.py` | Assistant token spans from a chat template; refuses to proceed if the template is not prefix-stable. |
| `rewards/step_reward.py` | Composite step reward: terminal, progress, grounding, safety, cost. Presets are the ablation surface. |
| `rewards/credit.py` | GAE / return-to-go over turns, broadcast onto the action tokens that earned it. Includes the naive flat-credit baseline. |
| `envs/multi_turn_env.py` | Chat-native wrapper over the L1 simulator; malformed output becomes a grounding violation, not a crash. Emits v2 records with provenance when given one. |
| `envs/l2_env.py` | Runs a live (L2) episode with the same chat surface and the same record shape. |
| `data/` | SFT corpus, environment-verified DPO pairs, the frozen benchmark, leakage checks. |
| `datasets.py` | Torch datasets and collators; refuses samples whose provenance disagrees with the run record. |
| `policies/` | Rule baseline, expert oracle, vLLM/HF backends, frozen OPD teacher (local or served) and its turn gate. |
| `trainers/` | Experience assembly and the PPO / OPD loss functions. |
| `train_{sft,dpo,ppo,opd}.py` | One CLI per stage, all driven by YAML in `configs/`. |
| `rollout.py` | Batched lockstep rollout; one generate call per turn for the whole batch. |
| `evaluate.py` | Metric set, failure taxonomy, bucket breakdowns over trajectories. |
| `evaluate_l2.py` | The same evaluator over the live stack, plus the receipt metrics only a live mission can produce. |
| `report.py` | `docs/results.md` generator: generated blocks, `unverified` cells, audit table, L2 status header. |
| `provenance.py` | Content hashes over prompt / schema / benchmark; refuses drifted samples. |
| `run_record.py` | The evidence a reported number requires. CLI hashes the *live* prompt surface and action schema. |
| `audit.py` | Reward-hacking signals: proxy gap, repeat exploitation, false success. |
| `counterfactual.py` | Counterfactuals only on a verified matched-state clone, L1 only. |
| `receipt_metrics.py` | Uncertain-outcome rate, duplicate-effect rate, latency percentiles from receipts. |
| `ablation.py` | Condition table with equal-budget enforcement. |
| `memory.py` | GPU memory arithmetic + the pre-flight that refuses an engine that cannot fit. |

## Quick start

Everything below the model stages runs on a laptop with no GPU:

```bash
# the frozen benchmark must match its generator
python -m training.data.build_benchmark --check

# score the two reference policies (no model needed)
python -m training.evaluate --policy rules  --label rule_baseline --out runs/eval/rule_baseline.json --markdown
python -m training.evaluate --policy expert --label expert_oracle --out runs/eval/expert_oracle.json

# a number is only evidence with a run record behind it
python -m training.run_record --name rule_baseline --kind eval \
    --model-id rule_baseline --policy rules --episodes 400 \
    --artifact results/eval/rule_baseline.json \
    --out results/run_records/rule_baseline.json

# refresh the tables inside docs/results.md, prose untouched
python -m training.report --update docs/results.md
python -m training.report --verify docs/results.md

# build the corpora
python -m training.data.build_sft --episodes 8000 --out data/sft
python -m training.data.build_dpo --episodes 3000 --granularity trajectory --out data/dpo
python -m training.data.dataset_stats data/sft/train.jsonl --benchmark benchmarks/fault_injection_v1.jsonl
```

The model stages need the cluster:

```bash
bash training/scripts/run_pipeline.sh              # everything, in order
bash training/scripts/run_pipeline.sh ppo_progress # or one stage
bash training/scripts/run_experiment_matrix.sh     # score every existing checkpoint, rebuild docs/results.md
```

The OPD arms additionally need the teacher served on its own cards -- it is 30B
(60.8 GB in bf16) and one copy per training rank cannot share a card with the
student:

```bash
bash training/scripts/serve_teacher.sh Qwen/Qwen3-30B-A3B 8100 4
```

## The four stages, and what each is for

**SFT** installs a behaviour prior: the skill schema, argument shapes, what a
ROS2 error code means, which recovery skills are legal after a given failure.
Loss is on assistant spans only — observations are inputs the environment always
supplies, never targets.

**DPO** learns the ordering SFT cannot express. Two trajectories both deliver the
tool; one takes eight steps, the other fourteen after three blind retries. The
pairs are built by *executing* the alternative in a cloned environment and
scoring both branches with the same composite reward PPO later optimizes, so a
plausible-looking "bad" action that turns out not to be worse never enters the
dataset. On a sample run, 93 `redundant_query` proposals were rejected exactly
this way.

**Step-level PPO** handles the states the corpora never contained. The reward is
grounded in the environment — delivered or not, navigation succeeded, grasp
verified, dangerous skill called, action count — not in an LLM judge.

**OPD** exists because the progress reward is *hand-designed*. Re-scanning shows
progress 0 in the short run while being the necessary recovery; a reward that
punishes it is misspecified. A frozen teacher scoring the student's own sampled
tokens gives dense supervision without anyone writing a reward for each recovery
state.

## Two distinctions the code makes explicit

**Dense reward is not credit assignment.** A process reward makes the signal
arrive more often. Credit assignment decides *which decision* the signal is
attributed to. `rewards/credit.py` does the second part — GAE over turns, then
the advantage scattered onto that turn's action tokens — and
`terminal_broadcast` implements the naive alternative so the ablation compares
against running code:

```bash
python -m training.train_ppo --config training/configs/ppo_progress.yaml \
  --set credit.terminal_broadcast=true --set run.output_dir=runs/ppo_progress_flatcredit
```

**Teacher preference is not environment truth.** The teacher reads a textual
observation; it does not know what the physics will do. It may prefer
repositioning where retrying the grasp would in fact have worked. Pure OPD can
therefore only approach the teacher, which is why `opd_warmup_ppo.yaml` decays
teacher weight from 1.0 → 0.1 while environment weight rises 0.2 → 1.0. The
crossover is a configured, logged schedule, not an implicit one.

## Reward presets

| Preset | Terminal | Progress | Grounding | Safety | Cost | Used for |
|---|---|---|---|---|---|---|
| `terminal_only` | ✓ | | | | | Outcome-only PPO baseline |
| `terminal_safety` | ✓ | | ✓ | ✓ | | Isolates the progress term |
| `progress` | ✓ | ✓ | ✓ | ✓ | ✓ | Main arm |
| `progress_no_grounding` | ✓ | ✓ | | ✓ | ✓ | Ablation |
| `progress_no_safety` | ✓ | ✓ | ✓ | | ✓ | Ablation |
| `progress_no_cost` | ✓ | ✓ | ✓ | ✓ | | Ablation |
| `progress_no_terminal` | | ✓ | ✓ | ✓ | ✓ | Shows the outcome still has to decide |

Calibration note: the no-progress penalty fires at **three** consecutive
non-advancing turns, not two. With faults injected, two can be entirely the
environment's doing — one retried navigation timeout, one re-perceived
low-confidence frame. Measured on the benchmark, the oracle never exceeds two
while the rule baseline reaches three or more on 17% of episodes, so a threshold
of two would have charged the policy for the fault.

## Evaluation

```bash
python -m training.evaluate --rollout runs/rollouts/<run>.jsonl --markdown
python -m training.report --update docs/results.md      # refresh the tables in place
python -m training.report --verify docs/results.md      # CI check: are the tables current?
python -m training.report --regression sft_dpo ppo_progress   # checkpoint regression
```

`docs/results.md` mixes hand-written prose with generated tables. The generated
tables live between `<!-- BEGIN GENERATED: name -->` markers and are owned by
`training/report.py`; everything outside the markers survives a regeneration. Two
rules keep the mechanism honest: a cell is only filled when a run record backs
the evaluation file (otherwise it reads `unverified`), and a regeneration that
would drop a measurement that is already published is refused rather than
applied.

Reported per run: task success, recovery success (fault-injected episodes only),
invalid skill rate, safety violation rate, mean high-level actions, repeated call
rate, no-progress ratio — plus one mutually exclusive terminal class per episode
(`premature_stop`, `false_success`, `wrong_cancel`, `step_limit`) and
non-exclusive behavioural flags, bucketed by fault family, failing phase and
trajectory length.

The failure decomposition is the actual result. "OPD reduced premature stops,
environment reward raised task success" is a claim about that table, not about a
single number.

## Split isolation

Train, dev and benchmark draw from disjoint seed blocks *and* disjoint operator
pools, so no exact mission string can appear in two splits. Every scenario id
ends with a content fingerprint, and `training/data/dataset_stats.py --benchmark`
intersects them; `build_benchmark --check` verifies the frozen file still matches
its generator. A number measured on a leaked benchmark is worse than no number.

## Status

The environment, data, reward, credit-assignment, rollout, evaluation and
reporting layers run and are covered by the test suite (`python -m pytest`).
The four model-training entry points are written against DeepSpeed ZeRO-3 + vLLM
but have **not** been executed on a GPU in this repository yet; their loss
functions and experience assembly are unit-tested on CPU, the distributed wiring
is not. `docs/results.md` shows which of the six arms have actually been run:
one, the rule baseline.

What *is* checked before a GPU run is the memory budget. `memory.py` lays out the
card the way the training loop actually uses it — the ZeRO-3 actor shard, the
unsharded reference copy, and the in-process rollout engine — and
`PPORunner.preflight_rollout_memory` measures the free memory with
`torch.cuda.mem_get_info()` once the trainer is resident. If the engine cannot
start, the run stops there with the number that would have worked:

```
the rollout engine does not fit next to the training state.
  rollout budget: card 96.0 GiB, training state 45.0 GiB, free 51.0 GiB; ...
  Fix: lower rollout.gpu_memory_utilization to 0.53 or below, ...
```

The two reference arms have been measured, and their artifacts are committed
under `results/` so the published table can be rebuilt from a clean checkout
with no GPU:

```bash
python -m training.evaluate --policy rules --label rule_baseline \
    --out runs/eval/rule_baseline.json
python -m training.report --update docs/results.md
```

The measured memory budget for each stage — and the two places where the
configuration did not fit until it was fixed — is in
[`docs/post_training.md`](../docs/post_training.md) §15.
