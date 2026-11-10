#!/usr/bin/env bash
# Score every arm that has a checkpoint, then refresh docs/results.md.
#
#   bash training/scripts/run_experiment_matrix.sh
#
# Arms without a checkpoint are skipped and stay "not run" in the report rather
# than silently disappearing from it. Evaluation is always greedy and always on
# the frozen benchmark, so two arms are comparable even when they were trained
# weeks apart.
#
# Each evaluation is paired with a run record: the report renders a number as
# `unverified` without one, because a metrics file on its own is not evidence.
# The record hashes the live prompt surface and the live action schema, so it
# cannot drift from what the run actually used.
set -euo pipefail

cd "$(dirname "$0")/../.."

BENCHMARK="benchmarks/fault_injection_v1.jsonl"
EVAL_DIR="runs/eval"
ROLLOUT_DIR="runs/rollouts"
RUN_RECORD_DIR="results/run_records"
# The student every trained arm starts from.
STUDENT_MODEL_ID="Qwen/Qwen3-8B"
mkdir -p "${EVAL_DIR}" "${ROLLOUT_DIR}" "${RUN_RECORD_DIR}"

# The benchmark must match its generator, or the numbers below are not comparable
# with anything previously reported.
python -m training.data.build_benchmark --check

record_arm() {
  local label="$1" model_id="$2" policy="$3"
  python -m training.run_record \
    --name "${label}" --kind eval --model-id "${model_id}" --policy "${policy}" \
    --benchmark "${BENCHMARK}" \
    --artifact "${EVAL_DIR}/${label}.json" \
    --out "${RUN_RECORD_DIR}/${label}.json"
}

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
  record_arm "${label}" "${STUDENT_MODEL_ID}" "vllm:${checkpoint}"
}

# Reference points that need no GPU: the deployed-rules reconstruction and the
# scripted oracle. The oracle is not an arm -- it produced the training labels --
# but its numbers bound what the environment allows.
python -m training.evaluate --policy rules --benchmark "${BENCHMARK}" \
  --label rule_baseline --out "${EVAL_DIR}/rule_baseline.json" --save-rollout "${ROLLOUT_DIR}/rule_baseline.jsonl"
record_arm rule_baseline rule_baseline rules

python -m training.evaluate --policy expert --benchmark "${BENCHMARK}" \
  --label expert_oracle --out "${EVAL_DIR}/expert_oracle.json" --save-rollout "${ROLLOUT_DIR}/expert_oracle.jsonl"
record_arm expert_oracle expert_oracle expert

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

# `--update` refreshes the generated tables inside the document and leaves the
# hand-written prose alone. It refuses to run if the result would drop a
# measurement that is already published.
python -m training.report --eval-dir results/eval "${EVAL_DIR}" --update docs/results.md
python -m training.report --eval-dir results/eval "${EVAL_DIR}" --verify docs/results.md
echo "report -> docs/results.md"
