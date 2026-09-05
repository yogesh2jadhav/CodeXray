# Evaluation baseline (retrieval mode — model-independent)

_Regenerate: `python scripts/run_eval.py --project synthetic --mode retrieval --model baseline`_


**60 cases · pass rate 100%**

| metric | value |
|--------|-------|
| pass_rate | 1.00 |
| retrieval_accuracy | 1.00 |
| evidence_accuracy | 1.00 |
| sql_resolution_accuracy | 1.00 |
| dependency_accuracy | 1.00 |
| latency_s | 0.00 |

## By category

| category | n | pass | retrieval | sql_res | dep | answer | halluc |
|----------|---|------|-----------|--------|-----|--------|--------|
| architecture | 12 | 100% | 1.00 | — | — | — | — |
| debugging | 5 | 100% | 1.00 | — | — | — | — |
| dependency | 6 | 100% | — | — | 1.00 | — | — |
| dynamic_sql | 10 | 100% | 1.00 | 1.00 | — | — | — |
| impact | 5 | 100% | 1.00 | — | 1.00 | — | — |
| java | 12 | 100% | 1.00 | — | — | — | — |
| sql | 10 | 100% | 1.00 | — | — | — | — |