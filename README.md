# dag

Deterministic **risk trunk** + DAG fan-out for agent tasks: split a goal into parallel branches, execute them against any OpenAI-compatible endpoint, verify each branch, then merge with evidence.

## Why

Most agent graphs call the model first and hope. `dag` does the opposite: a deterministic reflex layer classifies the goal, blocks catastrophic actions (`rm -rf /`, `drop table`, `mkfs`, …) **before** any model call, then fans out only what is allowed and records per-branch verification.

## Quick start

```bash
# simulation (no network): plan + fake branches + verify + merge
python3 dag.py "analiziraj trziste industrijskih masina" --levels 2,2

# real: any OpenAI-compatible endpoint(s)
export DAG_BULK_URL=http://127.0.0.1:8000/v1 DAG_BULK_MODEL=your-model
export DAG_SMART_URL=http://127.0.0.1:8000/v1 DAG_SMART_MODEL=your-model
python3 dag.py "napravi plan migracije baze" --levels 2,2 --real
```

Branches are independent: a failed or unverified branch does not block the others; the merge reports `verified X/Y`.

## Configuration (env)

`DAG_BULK_URL`, `DAG_BULK_MODEL`, `DAG_SMART_URL`, `DAG_SMART_MODEL`, `DAG_FREE_URL`, `DAG_FREE_MODEL`,
`DAG_BROWSER_URL`, `DAG_MEMORY_FILE`, `DAG_MEMORY_MODULE`, `DAG_HC_MODULE`.

Optional integrations are **opt-in modules**: set `DAG_HC_MODULE` (human-compute harness) or `DAG_MEMORY_MODULE`
(knowledge store) to import them; without them the DAG runs fully standalone.

## Guarantees

- Catastrophic intents are refused deterministically (no model call).
- Every branch is bounded by a timeout and verified; failures are reported, not hidden.
- Merge includes a short evidence line per branch (source + verification).
- No network unless `--real` and endpoints are configured.

## Limitations

- Verification is LLM-based unless you plug in your own checker; treat it as a filter, not proof.
- The reflex layer is pattern-based (ASCII-folded); extend `reflex.py` for your language.
- This is an orchestration skeleton, not a scheduler; durability is out of scope.

## License

Apache-2.0.
