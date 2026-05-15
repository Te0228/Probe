# Probe Benchmark Suite

Measures Probe's bug diagnosis accuracy and end-to-end trace completeness
across 5 standard bug fixture projects.

## Usage

```bash
python benchmarks/run_benchmark.py
```

Each fixture is run through the full ReAct loop. The benchmark scores:
- **Correct diagnosis**: whether the confirmed hypothesis matches the
  expected root cause category
- **Trace completeness**: JSONL and HTML files produced for every run

## Fixtures

| Fixture | Bug Type | Expected |
|---------|----------|----------|
| `type_mismatch` | `int + str` → TypeError | `type_mismatch` |
| `null_reference` | `.attr` on None → AttributeError | `null_reference` |
| `off_by_one` | Loop boundary → wrong count | `off_by_one` |
| `wrong_return_value` | `>` instead of `>=` | `wrong_return_value` |
| `import_error` | Missing module import | `import_error` |

## Scoring

- **Pass threshold**: ≥ 3 out of 5 correctly diagnosed
- **Full score (5/5)**: all bugs correctly identified with complete traces

## Output

After the run, a summary table is printed:

```
Fixture                Expected             Pass   Verdict        Time
---------------------------------------------------------------------
type_mismatch          type_mismatch        PASS   confirmed      42s
null_reference         null_reference       PASS   confirmed      42s
off_by_one             off_by_one           PASS   confirmed      43s
wrong_return_value     wrong_return_value   PASS   confirmed      43s
import_error           import_error         PASS   confirmed      43s
---------------------------------------------------------------------
  Score: 5/5
```

Traces are saved under `probe_traces/<session_id>/` for each run.
