# results/ — committed evidence

Everything in here is **evidence**, not scratch. It is what makes a number in
[`docs/results.md`](../docs/results.md) checkable.

| Path | What it is |
|---|---|
| `eval/<arm>.json` | The evaluation artifact `training.evaluate` wrote for that arm. |
| `run_records/<arm>.json` | The run record for it: model, prompt hash, action-schema hash, benchmark hash, seeds, budgets, artifacts. |

## Why these are in the repository

A metrics file on its own is not evidence — a number can be edited, copied from
a different run, or measured under a prompt that no longer exists. The run
record pins the exact prompt surface and action schema that produced it, and
`training/report.py` renders a cell as `unverified` when the record is missing.

The reference arms are the two policies that need no GPU:

```bash
python -m training.evaluate --policy rules --label rule_baseline \
    --benchmark benchmarks/fault_injection_v1.jsonl --out runs/eval/rule_baseline.json
python -m training.run_record --name rule_baseline --kind eval \
    --model-id rule_baseline --policy rules --episodes 400 \
    --artifact results/eval/rule_baseline.json \
    --out results/run_records/rule_baseline.json
python -m training.report --update docs/results.md
```

The L1 simulator and the rule policy contain no sampling, so the measurement is
reproducible bit for bit: rule baseline 76.1% recovery success, scripted
oracle 100%. A clean checkout can therefore rebuild the published table with no
GPU and no network, and CI checks that the document matches these files.
