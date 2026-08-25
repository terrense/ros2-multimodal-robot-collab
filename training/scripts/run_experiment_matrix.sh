#!/usr/bin/env bash
# Score every arm that has a checkpoint, then regenerate docs/results.md.
#
#   bash training/scripts/run_experiment_matrix.sh
#
# Arms without a checkpoint are skipped and stay "not run" in the report rather
# than silently disappearing from it. Evaluation is always greedy and always on
# the frozen benchmark, so two arms are comparable even when they were trained
# weeks apart.
set -euo pipefail

cd "$(dirname "$0")/../.."

BENCHMARK="benchmarks/fault_injection_v1.jsonl"
EVAL_DIR="runs/eval"
ROLLOUT_DIR="runs/rollouts"
mkdir -p "${EVAL_DIR}" "${ROLLOUT_DIR}"

# The benchmark must match its generator, or the numbers below are not comparable
# with anything previously reported.
python -m training.data.build_benchmark --check

evaluate_checkpoint() {
  local label="$1" checkpoint="$2"
  if [ ! -d "${checkpoint}" ]; then
    echo "skip ${label}: no checkpoint at ${checkpoint}"
    return
  fi
  echo "evaluating ${label} (${checkpoint})"
  python -m training.evaluate \
    --policy "vllm:${checkpoint}" \
    --benchmark "${BENCHMARK}" \
    --label "${label}" \
    --out "${EVAL_DIR}/${label}.json" \
    --save-rollout "${ROLLOUT_DIR}/${label}.jsonl"
}

# Reference points that need no GPU: the deployed-rules reconstruction and the
# scripted oracle. The oracle is not an arm -- it produced the training labels --
# but its numbers bound what the environment allows.
python -m training.evaluate --policy rules --benchmark "${BENCHMARK}" \
  --label rule_baseline --out "${EVAL_DIR}/rule_baseline.json" --save-rollout "${ROLLOUT_DIR}/rule_baseline.jsonl"
python -m training.evaluate --policy expert --benchmark "${BENCHMARK}" \
  --label expert_oracle --out "${EVAL_DIR}/expert_oracle.json" --save-rollout "${ROLLOUT_DIR}/expert_oracle.jsonl"

evaluate_checkpoint sft            runs/sft_qwen3_8b/final
evaluate_checkpoint sft_dpo        runs/dpo_qwen3_8b/final
evaluate_checkpoint ppo_terminal   runs/ppo_terminal/final
evaluate_checkpoint ppo_progress   runs/ppo_progress/final
evaluate_checkpoint opd_pure       runs/opd_pure/final
evaluate_checkpoint opd_warmup_ppo runs/opd_warmup_ppo/final

# Ablation checkpoints, present only once the corresponding run has been done.
evaluate_checkpoint ppo_progress_flatcredit  runs/ppo_progress_flatcredit/final
evaluate_checkpoint ppo_progress_nogrounding runs/ppo_progress_nogrounding/final
evaluate_checkpoint ppo_progress_nosafety    runs/ppo_progress_nosafety/final
evaluate_checkpoint ppo_progress_nocost      runs/ppo_progress_nocost/final

python -m training.report --eval-dir "${EVAL_DIR}" --out docs/results.md
echo "report -> docs/results.md"
