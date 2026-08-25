#!/usr/bin/env bash
# Full post-training pipeline, in the order the stages depend on each other.
#
#   bash training/scripts/run_pipeline.sh [stage ...]
#
# With no arguments it runs everything. Name stages to run a subset:
#
#   bash training/scripts/run_pipeline.sh data sft
#   bash training/scripts/run_pipeline.sh ppo_progress opd_warmup
#
# GPU budget note (4x H20): the arms are run one at a time on purpose. A PPO
# iteration already holds actor + reference + rollout engine; adding a 30B
# teacher on top only fits because the teacher is inference-only and MoE, and
# because nothing else is resident at the same time. Wall-clock goes up, the GPU
# count does not.
set -euo pipefail

cd "$(dirname "$0")/../.."

GPUS="${GPUS:-4}"
EPISODES_SFT="${EPISODES_SFT:-8000}"
EPISODES_DPO="${EPISODES_DPO:-3000}"

run_stage() {
  echo ""
  echo "=============================================================="
  echo "  $1"
  echo "=============================================================="
}

stage_data() {
  run_stage "data: benchmark, SFT corpus, DPO pairs"
  python -m training.data.build_benchmark --check
  python -m training.data.build_sft --episodes "${EPISODES_SFT}" --out data/sft
  python -m training.data.build_dpo --episodes "${EPISODES_DPO}" --granularity trajectory --out data/dpo
  # A leaked benchmark scenario invalidates every number below it.
  python -m training.data.dataset_stats data/sft/train.jsonl --benchmark benchmarks/fault_injection_v1.jsonl
}

stage_sft() {
  run_stage "SFT: behaviour prior"
  deepspeed --num_gpus "${GPUS}" -m training.train_sft --config training/configs/sft_qwen3_8b.yaml
}

stage_dpo() {
  run_stage "DPO: preference over verified pairs"
  deepspeed --num_gpus "${GPUS}" -m training.train_dpo --config training/configs/dpo_qwen3_8b.yaml
}

stage_ppo_terminal() {
  run_stage "PPO (terminal reward only) -- the sparse-credit baseline"
  deepspeed --num_gpus "${GPUS}" -m training.train_ppo --config training/configs/ppo_terminal.yaml
}

stage_ppo_progress() {
  run_stage "PPO (composite step reward + turn-level credit)"
  deepspeed --num_gpus "${GPUS}" -m training.train_ppo --config training/configs/ppo_progress.yaml
}

stage_opd_pure() {
  run_stage "OPD (teacher guidance only, no environment reward)"
  deepspeed --num_gpus "${GPUS}" -m training.train_opd --config training/configs/opd_pure.yaml
}

stage_opd_warmup() {
  run_stage "OPD warm-up -> progress PPO (the main hypothesis)"
  deepspeed --num_gpus "${GPUS}" -m training.train_opd --config training/configs/opd_warmup_ppo.yaml
}

stage_evaluate() {
  run_stage "evaluation + report"
  bash training/scripts/run_experiment_matrix.sh
}

STAGES=("$@")
if [ ${#STAGES[@]} -eq 0 ]; then
  STAGES=(data sft dpo ppo_terminal ppo_progress opd_pure opd_warmup evaluate)
fi

for stage in "${STAGES[@]}"; do
  "stage_${stage}"
done

echo ""
echo "pipeline finished: $(printf '%s ' "${STAGES[@]}")"
