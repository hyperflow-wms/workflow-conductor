# Experiment Data Capture Design

**Date:** 2026-03-12
**Purpose:** Automatically capture all data required by the Euro-Par 2026 paper experiment instructions (`paper/e2e-experiment-instructions.md`) so that experiment runs produce complete, machine-readable reports without manual log parsing.

## Current State

The conductor already captures:
- Phase timings (all 10 phases, via `state.phase_timings`)
- Task counts (total, completed, failed — via Sentinel)
- Chromosome data (actual variant row counts per chromosome — Phase 5)
- Workflow metrics (processes, signals, populations, chromosomes)

Missing:
1. **LLM usage** — token counts, model ID, API latency, cost
2. **Estimated vs actual comparison** — Phase 2 estimates vs Phase 5 actuals
3. **Cluster utilization** — `kubectl top` snapshots during execution
4. **Structured report** — no experiment report file generated

## Design

### 1. LLM Usage Tracking

**Key insight:** mcp-agent's `TokenCounter` is already active (`context.token_counter` initialized in `core/context.py:486`). The Anthropic LLM records `response.usage.input_tokens`/`output_tokens` every iteration. The conductor just doesn't read it.

**Approach:**
- After Phase 2 (planning), call `app.get_token_summary()` to get cumulative token usage and store in state
- Wrap `llm.generate_str()` with `time.monotonic()` to capture API latency
- Phase 6 (generation) is deterministic MCP tool call (no LLM) — record as zero LLM tokens
- For Sentinel's `_translate_to_nl` (Anthropic API), accumulate `response.usage` from each call

**New state field:**
```python
llm_usage: dict[str, Any] = Field(default_factory=dict)
# {
#   "planning": {
#     "model": "gemini-2.0-flash",
#     "input_tokens": 1234,
#     "output_tokens": 567,
#     "tool_calls": 3,
#     "latency_ms": 4500
#   },
#   "monitoring": {
#     "model": "claude-sonnet-4-20250514",
#     "input_tokens": 200,
#     "output_tokens": 150,
#     "api_calls": 12
#   }
# }
```

**Files changed:**
- `models.py` — add `llm_usage` field to `PipelineState`
- `app.py` — pass `MCPApp` instance to planning phase, read token summary after
- `planning.py` — time `generate_str()`, count tool calls from history, store in `state.llm_usage["planning"]`
- `monitoring.py` — accumulate `response.usage` from `_translate_to_nl`, store in `state.llm_usage["monitoring"]`

### 2. Estimated vs Actual Comparison (Deferred Generation Data)

**Approach:** The Composer's `plan_workflow` MCP tool returns a raw plan JSON containing:
- `data_preparation.estimated_transfer_mb`
- `execution_hints.recommended_parallelism`
- Estimated variant counts (if available in the plan)
- Estimated task count

Extract these during planning and store them. The report generator compares with Phase 5 actuals (`state.chromosome_data[*].row_count`) and Phase 6 actuals (workflow process count).

**New state field:**
```python
planning_estimates: dict[str, Any] = Field(default_factory=dict)
# {
#   "estimated_transfer_mb": 150,
#   "estimated_tasks": 340,
#   "estimated_parallelism": 10,
#   "estimated_variants": {"6": 50000, "17": 3000}
# }
```

**Files changed:**
- `models.py` — add `planning_estimates` field to `PipelineState`
- `planning.py` — extract estimates from `raw_plan` into `state.planning_estimates`

### 3. Cluster Utilization Snapshots

**Approach:** During Phase 9 (monitoring), run `kubectl top nodes` and `kubectl top pods -n <ns>` periodically alongside the Sentinel polling loop. Capture snapshots every ~30 seconds, store in state for the report.

**New state field:**
```python
cluster_snapshots: list[dict[str, Any]] = Field(default_factory=list)
# [
#   {
#     "timestamp": "2026-03-12T14:30:00",
#     "nodes": "NAME  CPU  MEMORY\nnode-4  12%  45%\n...",
#     "pods": "NAME  CPU  MEMORY\njob-individuals-1  500m  256Mi\n..."
#   }
# ]
```

**Files changed:**
- `models.py` — add `cluster_snapshots` field to `PipelineState`
- `monitoring.py` — add background task that runs `kubectl top` alongside `sentinel.watch()`, append snapshots to state

### 4. Experiment Report Generation

**Approach:** New module `src/workflow_conductor/reporting.py` that formats all state data into the markdown template from `paper/e2e-experiment-instructions.md`. Called from `completion.py` when `experiment_report_path` is configured.

**New config:**
```python
experiment_report_path: str = ""
# If non-empty, write the experiment report markdown to this path.
# Supports {query_id} and {date} placeholders:
#   e.g., "/path/to/research/e2e-results-{date}.md"
```

**Report sections (matching the template):**
1. Intent Interpretation — from `state.workflow_plan` and `state.intent_classification`
2. Phase Timing — from `state.phase_timings`, grouped into LLM/Infrastructure/Execution/Overhead
3. Workflow Metrics — from `state.workflow_json`, `state.workflow_plan`
4. Deferred Generation — `state.planning_estimates` vs `state.chromosome_data` actuals
5. LLM Usage — from `state.llm_usage`
6. Cluster Snapshot — peak utilization from `state.cluster_snapshots`
7. Raw Artifacts — log file path, namespace

**Files changed:**
- `config.py` — add `experiment_report_path` setting
- New file `reporting.py` — report formatting logic
- `completion.py` — call `write_experiment_report()` if path configured

## Files Summary

| File | Change |
|------|--------|
| `src/workflow_conductor/models.py` | Add `llm_usage`, `planning_estimates`, `cluster_snapshots` fields |
| `src/workflow_conductor/config.py` | Add `experiment_report_path` setting |
| `src/workflow_conductor/app.py` | Pass MCPApp to planning, read token summary |
| `src/workflow_conductor/phases/planning.py` | Time LLM call, count tool calls, extract estimates |
| `src/workflow_conductor/phases/monitoring.py` | Accumulate `_translate_to_nl` tokens, run `kubectl top` |
| `src/workflow_conductor/phases/completion.py` | Call report writer |
| `src/workflow_conductor/reporting.py` | **New** — experiment report generation |
| `tests/unit/test_reporting.py` | **New** — report generation tests |

## Non-Goals

- No changes to the pipeline flow or phase ordering
- No new dependencies (uses existing mcp-agent TokenCounter, kubectl, Anthropic SDK)
- Manual comparison data (Section 6 of template) remains manual — filled by co-authors separately
