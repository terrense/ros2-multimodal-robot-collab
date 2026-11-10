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
# iteration already holds actor + reference + rollout engine on every card.
#
# The OPD arms add the 30B teacher, and that teacher does NOT go inside the
# training process: it is 60.8 GB in bf16, and one copy per training rank
# cannot share a card with the student. It runs as its own vLLM server on its
# own cards, and the ranks ask it for log-probabilities over HTTP. So the OPD
# arms need the student GPUs plus a separate teacher GPU group -- the pipeline
# checks the server is up and tells you how to start it.
set -euo pipefail

cd "$(dirname "$0")/../.."

GPUS="${GPUS:-4}"
EPISODES_SFT="${EPISODES_SFT:-8000}"
EPISODES_DPO="${EPISODES_DPO:-3000}"
TEACHER_MODEL="${TEACHER_MODEL:-Qwen/Qwen3-30B-A3B}"
TEACHER_PORT="${TEACHER_PORT:-8100}"
TEACHER_TP="${TEACHER_TP:-2}"
TEACHER_URL="${TEACHER_URL:-http://127.0.0.1:${TEACHER_PORT}/v1}"

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

require_teacher() {
  # The teacher lives in its own process. Starting it is a deliberate step:
  # it needs its own cards, and the run must not silently score against
  # whatever happens to be listening on the port.
  if command -v curl > /dev/null 2>&1; then
    if curl -sf "${TEACHER_URL}/models" > /dev/null 2>&1; then
      echo "teacher is up at ${TEACHER_URL}"
      return 0
    fi
    cat >&2 <<EOF
The OPD arms need the teacher served at ${TEACHER_URL}, and nothing is answering there.

Start it on its own cards:

  bash training/scripts/serve_teacher.sh ${TEACHER_MODEL} ${TEACHER_PORT} ${TEACHER_TP}

The teacher is 30B -- 60.8 GB in bf16 -- and one copy per training rank cannot
share a card with the student, so it gets its own process and its own cards.
EOF
    exit 1
  fi
  echo "curl is not installed: skipping the teacher check, hoping ${TEACHER_URL} is up"
}

stage_opd_pure() {
  run_stage "OPD (teacher guidance only, no environment reward)"
  require_teacher
  deepspeed --num_gpus "${GPUS}" -m training.train_opd --config training/configs/opd_pure.yaml
}

stage_opd_warmup() {
  run_stage "OPD warm-up -> progress PPO (the main hypothesis)"
  require_teacher
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
