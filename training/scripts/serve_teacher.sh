#!/usr/bin/env bash
# Serve the frozen teacher as its own vLLM process.
#
#   bash training/scripts/serve_teacher.sh [MODEL] [PORT] [TENSOR_PARALLEL]
#
# The teacher is 30B: 60.8 GB in bf16. The training run already holds the
# student, its gradients and its optimizer state on its own cards, and one
# teacher copy per rank cannot share a card with them. So the teacher gets its
# own process, on its own cards, and the ranks ask it for log-probabilities
# over HTTP -- see training/policies/teacher.py::RemoteTeacherScorer.
#
# The port must match `teacher.base_url` in the OPD config.
#
# Environment:
#   VLLM_MAX_MODEL_LEN           default 8192
#   VLLM_GPU_MEMORY_UTILIZATION  default 0.85 (the teacher has a card to itself)
set -euo pipefail

MODEL="${1:-Qwen/Qwen3-30B-A3B}"
PORT="${2:-8100}"
TP="${3:-4}"

export VLLM_WORKER_MULTIPROC_METHOD=spawn

echo "teacher: ${MODEL} on port ${PORT}, tensor parallel ${TP}"

exec python -m vllm.entrypoints.openai.api_server \
    --model "${MODEL}" \
    --served-model-name "${MODEL}" \
    --port "${PORT}" \
    --tensor-parallel-size "${TP}" \
    --dtype bfloat16 \
    --max-model-len "${VLLM_MAX_MODEL_LEN:-8192}" \
    --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.85}"
