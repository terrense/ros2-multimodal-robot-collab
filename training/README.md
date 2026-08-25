# training — post-training stack for the delivery agent

The high-level policy is an LLM that emits one skill call per turn. This
directory turns that into something trainable: a chat surface, a reward, a credit
assignment scheme, four training stages and an evaluation that can say *what*
went wrong rather than only how often.

The environment itself lives in [`agent_env/`](../agent_env/README.md).

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
aiming at.

## Layout

| Module | Role |
|---|---|
| `chat_format.py` | The only prompt surface. Observation → messages, text → `Action`, with forgiving *format* parsing and strict *content* validation. |
| `tokenization.py` | Assistant token spans from a chat template; refuses to proceed if the template is not prefix-stable. |
| `rewards/step_reward.py` | Composite step reward: terminal, progress, grounding, safety, cost. Presets are the ablation surface. |
| `rewards/credit.py` | GAE / return-to-go over turns, broadcast onto the action tokens that earned it. Includes the naive flat-credit baseline for comparison. |
| `envs/multi_turn_env.py` | Chat-native wrapper over the L1 simulator; malformed output becomes a grounding violation, not a crash. |
| `data/` | SFT corpus, environment-verified DPO pairs, the frozen benchmark, leakage checks. |
| `policies/` | Rule baseline, expert oracle, vLLM/HF backends, frozen OPD teacher and its turn gate. |
| `trainers/` | Experience assembly and the PPO / OPD loss functions. |
| `train_{sft,dpo,ppo,opd}.py` | One CLI per stage, all driven by YAML in `configs/`. |
| `rollout.py` | Batched lockstep rollout; one generate call per turn for the whole batch. |
| `evaluate.py` / `report.py` | Metric set, failure taxonomy, bucket breakdowns, comparison tables. |

## Quick start

Everything below the model stages runs on a laptop with no GPU:

```bash
# the frozen benchmark must match its generator
python -m training.data.build_benchmark --check

# score the two reference policies (no model needed)
python -m training.evaluate --policy rules  --label rule_baseline --out runs/eval/rule_baseline.json --markdown
python -m training.evaluate --policy expert --label expert_oracle --out runs/eval/expert_oracle.json

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
python -m training.report --eval-dir runs/eval --out docs/results.md
python -m training.report --regression sft_dpo ppo_progress   # checkpoint regression
```

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
reporting layers run and are covered by the test suite (`python -m pytest`). The
four model-training entry points are written against DeepSpeed ZeRO-3 + vLLM but
have **not** been executed on a GPU in this repository yet; their loss functions
and experience assembly are unit-tested on CPU, the distributed wiring is not.
`docs/results.md` shows which arms have actually been run.
