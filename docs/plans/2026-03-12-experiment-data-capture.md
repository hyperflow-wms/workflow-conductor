# Experiment Data Capture — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically capture all Euro-Par 2026 paper experiment data (LLM tokens, estimated vs actual comparisons, cluster utilization, structured markdown report) so each pipeline run produces a complete experiment report.

**Architecture:** Add 3 new state fields (`llm_usage`, `planning_estimates`, `cluster_snapshots`) populated by instrumentation in planning, monitoring, and a new `kubectl top` background task. A new `reporting.py` module formats state into the markdown template and writes it from completion phase.

**Tech Stack:** Pydantic state fields, Anthropic SDK `response.usage`, Google `response.usage_metadata`, asyncio background tasks, kubectl subprocess, Jinja-free string formatting.

---

### Task 1: Add state fields to `PipelineState`

**Files:**
- Modify: `src/workflow_conductor/models.py:214` (after `# Audit trail` comment)
- Test: `tests/unit/test_models.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_models.py`:

```python
class TestExperimentDataFields:
    def test_llm_usage_default_empty(self) -> None:
        state = PipelineState()
        assert state.llm_usage == {}

    def test_planning_estimates_default_empty(self) -> None:
        state = PipelineState()
        assert state.planning_estimates == {}

    def test_cluster_snapshots_default_empty(self) -> None:
        state = PipelineState()
        assert state.cluster_snapshots == []

    def test_experiment_fields_serialize(self) -> None:
        state = PipelineState()
        state.llm_usage = {"planning": {"input_tokens": 100, "output_tokens": 50}}
        state.planning_estimates = {"estimated_tasks": 340}
        state.cluster_snapshots = [{"timestamp": "2026-03-12T14:00:00", "nodes": "..."}]
        data = state.model_dump()
        assert data["llm_usage"]["planning"]["input_tokens"] == 100
        assert data["planning_estimates"]["estimated_tasks"] == 340
        assert len(data["cluster_snapshots"]) == 1

        # Round-trip via JSON
        restored = PipelineState.from_json(state.to_json())
        assert restored.llm_usage == state.llm_usage
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py::TestExperimentDataFields -v`
Expected: FAIL — `PipelineState` has no attribute `llm_usage`

**Step 3: Write minimal implementation**

In `src/workflow_conductor/models.py`, add after line 218 (`phase_timings`), before `errors`:

```python
    # Experiment data capture (for paper reporting)
    llm_usage: dict[str, Any] = Field(default_factory=dict)
    planning_estimates: dict[str, Any] = Field(default_factory=dict)
    cluster_snapshots: list[dict[str, Any]] = Field(default_factory=list)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models.py::TestExperimentDataFields -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/models.py tests/unit/test_models.py
git commit -m "feat: add experiment data capture fields to PipelineState"
```

---

### Task 2: Add `experiment_report_path` to config

**Files:**
- Modify: `src/workflow_conductor/config.py:92` (end of ConductorSettings)
- Test: `tests/unit/test_config.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_experiment_report_path_default_empty() -> None:
    settings = ConductorSettings()
    assert settings.experiment_report_path == ""

def test_experiment_report_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_CONDUCTOR_EXPERIMENT_REPORT_PATH", "/tmp/report.md")
    settings = ConductorSettings()
    assert settings.experiment_report_path == "/tmp/report.md"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_experiment_report_path_default_empty -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `src/workflow_conductor/config.py`, add after line 92 (`monitor_timeout`):

```python
    experiment_report_path: str = ""
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -k experiment_report -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/config.py tests/unit/test_config.py
git commit -m "feat: add experiment_report_path config setting"
```

---

### Task 3: Capture LLM usage in planning phase

**Files:**
- Modify: `src/workflow_conductor/phases/planning.py:183-281`
- Test: `tests/unit/test_planning.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_planning.py` (adapt existing mock pattern):

```python
class TestLLMUsageCapture:
    """Test that planning phase captures LLM usage metrics."""

    @pytest.fixture()
    def state_with_plan(self) -> PipelineState:
        return PipelineState(user_prompt="Analyze BRCA1 in GBR")

    async def test_llm_usage_captured(
        self, state_with_plan: PipelineState, mock_composer_agent: Any
    ) -> None:
        """After planning, state.llm_usage['planning'] should have token counts."""
        settings = ConductorSettings()
        state = await run_planning_phase(state_with_plan, settings)
        planning_usage = state.llm_usage.get("planning", {})
        assert "latency_ms" in planning_usage
        assert planning_usage["latency_ms"] >= 0
```

Note: The exact mock setup depends on existing test fixtures in `test_planning.py`. The key assertion is that `state.llm_usage["planning"]` gets populated.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_planning.py::TestLLMUsageCapture -v`
Expected: FAIL — `llm_usage` is empty dict

**Step 3: Write minimal implementation**

In `src/workflow_conductor/phases/planning.py`, modify `run_planning_phase()`:

1. Add `import time` at top
2. Wrap the `generate_str()` call with timing:

```python
    async with composer_agent:
        llm = await composer_agent.attach_llm(llm_class)

        prompt = state.synthesize_context_for_composer()
        start_ms = time.monotonic()
        response = await llm.generate_str(
            message=f"Plan a workflow for: {prompt}",
            request_params=RequestParams(max_iterations=5),
        )
        latency_ms = int((time.monotonic() - start_ms) * 1000)
```

3. After extracting plan data but before return, count tool calls and store usage:

```python
    # Count tool calls from history
    tool_call_count = 0
    if hasattr(llm, "history"):
        with contextlib.suppress(Exception):
            for msg in llm.history.get():
                if hasattr(msg, "parts") and msg.parts:
                    for part in msg.parts:
                        fc = getattr(part, "function_call", None)
                        if fc:
                            tool_call_count += 1

    state.llm_usage["planning"] = {
        "model": settings.llm.google_model if provider == "google" else settings.llm.anthropic_model,
        "provider": provider,
        "latency_ms": latency_ms,
        "tool_calls": tool_call_count,
    }
```

Note: Google Gemini's `response` object from mcp-agent doesn't expose `usage_metadata` (mcp-agent drops it). Token counts for Google come from `response.usage_metadata.prompt_token_count` / `candidates_token_count` on the raw response, but mcp-agent doesn't surface this. For now, capture model + latency + tool_calls. Token counts will be available when using Anthropic provider (which does track them via `response.usage`).

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_planning.py::TestLLMUsageCapture -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/phases/planning.py tests/unit/test_planning.py
git commit -m "feat: capture LLM usage metrics in planning phase"
```

---

### Task 4: Extract planning estimates for deferred generation comparison

**Files:**
- Modify: `src/workflow_conductor/phases/planning.py:258-267`
- Test: `tests/unit/test_planning.py`

**Step 1: Write the failing test**

```python
class TestPlanningEstimates:
    async def test_estimates_extracted_from_raw_plan(self) -> None:
        """Planning should extract estimated_transfer_mb and task counts from raw_plan."""
        state = PipelineState(user_prompt="test")
        # Simulate a raw_plan with estimates
        state.workflow_plan = WorkflowPlan(
            chromosomes=["17"],
            populations=["GBR"],
            estimated_data_size_gb=0.5,
            raw_plan={
                "data_preparation": {"estimated_transfer_mb": 512},
                "execution_hints": {"recommended_parallelism": 10},
                "estimated_task_count": 47,
            },
        )
        # The extraction happens inside run_planning_phase, but we can test
        # the extraction logic directly
        from workflow_conductor.phases.planning import _extract_planning_estimates
        estimates = _extract_planning_estimates(state.workflow_plan.raw_plan)
        assert estimates["estimated_transfer_mb"] == 512
        assert estimates["estimated_parallelism"] == 10
        assert estimates["estimated_tasks"] == 47
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_planning.py::TestPlanningEstimates -v`
Expected: FAIL — `_extract_planning_estimates` does not exist

**Step 3: Write minimal implementation**

Add to `planning.py`:

```python
def _extract_planning_estimates(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Extract estimated metrics from the Composer's raw plan for experiment reporting."""
    estimates: dict[str, Any] = {}
    dp = raw_plan.get("data_preparation", {})
    if dp.get("estimated_transfer_mb"):
        estimates["estimated_transfer_mb"] = dp["estimated_transfer_mb"]
    hints = raw_plan.get("execution_hints", {})
    if hints.get("recommended_parallelism"):
        estimates["estimated_parallelism"] = hints["recommended_parallelism"]
    if raw_plan.get("estimated_task_count"):
        estimates["estimated_tasks"] = raw_plan["estimated_task_count"]
    # Extract per-chromosome variant estimates if available
    for step in dp.get("steps", []):
        if step.get("estimated_variants"):
            chrom = step.get("chromosome", "")
            if chrom:
                estimates.setdefault("estimated_variants", {})[chrom] = step["estimated_variants"]
    return estimates
```

Then in `run_planning_phase()`, after building `state.workflow_plan`, add:

```python
    state.planning_estimates = _extract_planning_estimates(raw_plan)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_planning.py::TestPlanningEstimates -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/phases/planning.py tests/unit/test_planning.py
git commit -m "feat: extract planning estimates for deferred generation comparison"
```

---

### Task 5: Capture `_translate_to_nl` token usage in monitoring

**Files:**
- Modify: `src/workflow_conductor/phases/monitoring.py:91-113` (`_translate_to_nl`) and `149-157` (drain loop)
- Test: `tests/unit/test_monitoring.py`

**Step 1: Write the failing test**

```python
class TestMonitoringLLMUsage:
    async def test_translate_to_nl_accumulates_tokens(self) -> None:
        """_translate_to_nl should return (text, usage_dict)."""
        from workflow_conductor.phases.monitoring import _translate_to_nl
        from unittest.mock import AsyncMock, patch, MagicMock

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Task completed successfully")]
        mock_response.usage = MagicMock(input_tokens=50, output_tokens=20)
        mock_response.model = "claude-sonnet-4-20250514"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        report = MagicMock()
        report.kind = "progress"
        report.message = "5/10 tasks done"
        report.completed_tasks = 5
        report.total_tasks = 10

        settings = ConductorSettings()

        with patch("workflow_conductor.phases.monitoring.anthropic") as mock_anthropic:
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            text, usage = await _translate_to_nl(report, settings)

        assert text == "Task completed successfully"
        assert usage["input_tokens"] == 50
        assert usage["output_tokens"] == 20
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_monitoring.py::TestMonitoringLLMUsage -v`
Expected: FAIL — `_translate_to_nl` returns str, not tuple

**Step 3: Write minimal implementation**

Change `_translate_to_nl` signature to return `tuple[str, dict[str, Any]]`:

```python
async def _translate_to_nl(
    report: SentinelReport, settings: ConductorSettings
) -> tuple[str, dict[str, Any]]:
    """Translate a Sentinel report into a science-friendly sentence.

    Returns (translated_text, usage_dict) where usage_dict contains
    input_tokens, output_tokens, model for experiment data capture.
    """
    try:
        import anthropic

        client = anthropic.AsyncAnthropic()
        prompt = (
            f"You are an AI assistant helping a genomics researcher. "
            f"Translate this Kubernetes workflow monitoring event into one clear, "
            f"science-friendly sentence (no Kubernetes jargon).\n"
            f"Event: kind={report.kind}, message={report.message!r}, "
            f"progress={report.completed_tasks}/{report.total_tasks}."
        )
        response = await client.messages.create(
            model=settings.llm.anthropic_model,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result: str = response.content[0].text.strip()
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model": settings.llm.anthropic_model,
        }
        return result, usage
    except Exception:
        return str(report.message), {}
```

Update the drain loop in `run_monitoring_phase` to accumulate usage:

```python
    monitoring_llm_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}

    async def _drain() -> None:
        while True:
            report = await sentinel.reports.get()
            text, usage = await _translate_to_nl(report, settings)
            report.nl_message = text
            if usage:
                monitoring_llm_usage["input_tokens"] += usage.get("input_tokens", 0)
                monitoring_llm_usage["output_tokens"] += usage.get("output_tokens", 0)
                monitoring_llm_usage["api_calls"] += 1
                monitoring_llm_usage["model"] = usage.get("model", "")
            display_report(report)
```

After `sentinel.watch()` completes, store in state:

```python
    if monitoring_llm_usage["api_calls"] > 0:
        state.llm_usage["monitoring"] = monitoring_llm_usage
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_monitoring.py::TestMonitoringLLMUsage -v`
Expected: PASS

**Step 5: Run full monitoring tests**

Run: `uv run pytest tests/unit/test_monitoring.py -v`
Expected: PASS (existing tests may need minor updates for the new return type)

**Step 6: Commit**

```bash
git add src/workflow_conductor/phases/monitoring.py tests/unit/test_monitoring.py
git commit -m "feat: capture LLM token usage from sentinel NL translations"
```

---

### Task 6: Add cluster utilization snapshots during monitoring

**Files:**
- Modify: `src/workflow_conductor/phases/monitoring.py:115-189`
- Test: `tests/unit/test_monitoring.py`

**Step 1: Write the failing test**

```python
class TestClusterSnapshots:
    async def test_cluster_snapshots_captured(self) -> None:
        """Monitoring should capture kubectl top snapshots."""
        from workflow_conductor.phases.monitoring import _capture_cluster_snapshot
        from unittest.mock import AsyncMock

        mock_kubectl = AsyncMock()
        mock_kubectl.run = AsyncMock(side_effect=[
            "NAME       CPU   MEMORY\nnode-4     12%   45%",
            "NAME       CPU   MEMORY\njob-1      500m  256Mi",
        ])

        snapshot = await _capture_cluster_snapshot(mock_kubectl, "test-ns")
        assert "nodes" in snapshot
        assert "pods" in snapshot
        assert "timestamp" in snapshot
        assert "node-4" in snapshot["nodes"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_monitoring.py::TestClusterSnapshots -v`
Expected: FAIL — `_capture_cluster_snapshot` does not exist

**Step 3: Write minimal implementation**

Add to `monitoring.py`:

```python
async def _capture_cluster_snapshot(
    kubectl: Any, namespace: str
) -> dict[str, str]:
    """Capture a point-in-time cluster utilization snapshot via kubectl top."""
    snapshot: dict[str, str] = {
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        nodes_output = await kubectl.run(["top", "nodes", "--no-headers"])
        snapshot["nodes"] = nodes_output.strip()
    except Exception:
        snapshot["nodes"] = ""
    try:
        pods_output = await kubectl.run(
            ["top", "pods", "-n", namespace, "--no-headers"]
        )
        snapshot["pods"] = pods_output.strip()
    except Exception:
        snapshot["pods"] = ""
    return snapshot
```

Add `from datetime import UTC, datetime` import. Add `from workflow_conductor.k8s import Kubectl` import.

In `run_monitoring_phase`, add a background snapshot task alongside the sentinel watch:

```python
    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)

    async def _snapshot_loop() -> None:
        """Capture cluster utilization every 30 seconds."""
        while True:
            await asyncio.sleep(30)
            snapshot = await _capture_cluster_snapshot(kubectl, state.namespace)
            state.cluster_snapshots.append(snapshot)

    snapshot_task = asyncio.create_task(_snapshot_loop())
    drain_task = asyncio.create_task(_drain())
    summary = await sentinel.watch()
    drain_task.cancel()
    snapshot_task.cancel()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_monitoring.py::TestClusterSnapshots -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/phases/monitoring.py tests/unit/test_monitoring.py
git commit -m "feat: capture cluster utilization snapshots during monitoring"
```

---

### Task 7: Create experiment report generator

**Files:**
- Create: `src/workflow_conductor/reporting.py`
- Test: `tests/unit/test_reporting.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_reporting.py`:

```python
"""Tests for experiment report generation."""

from __future__ import annotations

import re

from workflow_conductor.models import (
    ChromosomeData,
    ExecutionSummary,
    PipelineState,
    PipelineStatus,
    WorkflowPlan,
)
from workflow_conductor.reporting import generate_experiment_report


class TestReportGeneration:
    def _make_state(self) -> PipelineState:
        """Build a minimal state with experiment data."""
        state = PipelineState(
            user_prompt="Analyze BRCA1 gene variants in the British population.",
            execution_id="20260312-140000",
            status=PipelineStatus.COMPLETED,
            workflow_status="completed",
            namespace="wf-1000g-abc123",
            total_task_count=47,
            task_completion_count=47,
        )
        state.workflow_plan = WorkflowPlan(
            chromosomes=["17"],
            populations=["GBR"],
            parallelism=10,
            estimated_data_size_gb=0.5,
            description="BRCA1 region analysis",
        )
        state.chromosome_data = [
            ChromosomeData(
                chromosome="17",
                vcf_file="ALL.chr17.brca1.vcf",
                row_count=81271,
                annotation_file="ALL.chr17.brca1.annotation.vcf",
            ),
        ]
        state.phase_timings = {
            "routing": 0.1,
            "planning": 5.2,
            "validation": 0.0,
            "provisioning": 30.5,
            "data_preparation": 15.3,
            "generation": 2.1,
            "approval": 0.0,
            "deployment": 3.8,
            "monitoring": 180.0,
            "completion": 2.0,
        }
        state.llm_usage = {
            "planning": {
                "model": "gemini-2.0-flash",
                "provider": "google",
                "latency_ms": 4500,
                "tool_calls": 3,
            },
            "monitoring": {
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 600,
                "output_tokens": 240,
                "api_calls": 12,
            },
        }
        state.planning_estimates = {
            "estimated_transfer_mb": 512,
            "estimated_parallelism": 10,
            "estimated_tasks": 50,
        }
        state.execution_summary = ExecutionSummary(
            total_tasks=47,
            completed_tasks=47,
            failed_tasks=0,
            total_runtime_seconds=239.0,
        )
        state.workflow_json = {
            "name": "1000genome-brca1",
            "processes": [{"name": f"task_{i}", "fun": "individuals"} for i in range(47)],
            "signals": [{"name": f"sig_{i}"} for i in range(46)],
        }
        return state

    def test_report_contains_query(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "Analyze BRCA1 gene variants" in report

    def test_report_contains_phase_timings(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "routing" in report.lower()
        assert "monitoring" in report.lower()
        assert "180.0" in report

    def test_report_contains_grouped_timings(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "LLM" in report
        assert "Infrastructure" in report
        assert "Execution" in report

    def test_report_contains_llm_usage(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "gemini-2.0-flash" in report
        assert "4500" in report  # latency_ms

    def test_report_contains_workflow_metrics(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "47" in report  # total processes
        assert "46" in report  # signals
        assert "GBR" in report

    def test_report_contains_deferred_generation(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        assert "81271" in report  # actual row count
        assert "Deferred" in report or "Variant" in report

    def test_report_is_valid_markdown(self) -> None:
        state = self._make_state()
        report = generate_experiment_report(state)
        # Has a top-level heading
        assert report.startswith("# ")
        # Has table separators
        assert "---" in report
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_reporting.py -v`
Expected: FAIL — `workflow_conductor.reporting` does not exist

**Step 3: Write minimal implementation**

Create `src/workflow_conductor/reporting.py`:

```python
"""Experiment report generation for Euro-Par 2026 paper data collection.

Formats PipelineState into the markdown template defined in
paper/e2e-experiment-instructions.md.
"""

from __future__ import annotations

import re
from typing import Any

from workflow_conductor.models import PipelineState


def generate_experiment_report(state: PipelineState) -> str:
    """Generate a markdown experiment report from pipeline state."""
    lines: list[str] = []

    # Header
    lines.append(f"# E2E Experiment Results — {state.execution_id}")
    lines.append("")
    lines.append(f"**Date:** {state.created_at}")
    lines.append(f'**Query:** "{state.user_prompt}"')

    # Cluster info
    if state.infrastructure:
        lines.append(
            f"**Cluster:** {state.infrastructure.node_count} nodes, "
            f"{state.infrastructure.available_vcpus} vCPUs, "
            f"{state.infrastructure.memory_gb:.0f} GB RAM"
        )
    lines.append(
        f"**LLM Model:** {state.llm_usage.get('planning', {}).get('model', 'N/A')}"
    )
    lines.append(f"**Namespace:** {state.namespace}")
    lines.append(f"**Status:** {state.status.value}")
    lines.append("")

    # Section 1: Intent Interpretation
    lines.append("## 1. Intent Interpretation")
    lines.append("")
    wp = state.workflow_plan
    if wp:
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Populations | {', '.join(wp.populations)} |")
        lines.append(f"| Chromosomes | {', '.join(wp.chromosomes)} |")
        lines.append(f"| Parallelism | {wp.parallelism} |")
        lines.append(f"| Description | {wp.description} |")
    lines.append("")

    # Section 2: Phase Timing
    lines.append("## 2. Phase Timing (seconds)")
    lines.append("")
    lines.append("| Phase | Duration (s) |")
    lines.append("|-------|-------------|")
    total = 0.0
    for phase_name in [
        "routing", "planning", "validation", "provisioning",
        "data_preparation", "generation", "approval",
        "deployment", "monitoring", "completion",
    ]:
        dur = state.phase_timings.get(phase_name, 0.0)
        total += dur
        label = phase_name.replace("_", " ").title()
        note = ""
        if phase_name in ("validation", "approval"):
            note = " (auto-approve)" if dur == 0 else ""
        lines.append(f"| {label} | {dur:.1f}{note} |")
    lines.append(f"| **Total** | **{total:.1f}** |")
    lines.append("")

    # Grouped timings
    llm_time = state.phase_timings.get("planning", 0) + state.phase_timings.get("generation", 0)
    infra_time = (
        state.phase_timings.get("provisioning", 0)
        + state.phase_timings.get("data_preparation", 0)
        + state.phase_timings.get("deployment", 0)
    )
    exec_time = state.phase_timings.get("monitoring", 0)
    overhead = state.phase_timings.get("routing", 0) + state.phase_timings.get("completion", 0)

    lines.append("### Grouped")
    lines.append("")
    lines.append("| Category | Duration (s) | % of total |")
    lines.append("|----------|-------------|------------|")
    for cat, dur in [
        ("LLM (Planning+Generation)", llm_time),
        ("Infrastructure (Provisioning+DataPrep+Deployment)", infra_time),
        ("Execution (Monitoring)", exec_time),
        ("Overhead (Routing+Completion)", overhead),
    ]:
        pct = (dur / total * 100) if total > 0 else 0
        lines.append(f"| {cat} | {dur:.1f} | {pct:.1f}% |")
    lines.append("")

    # Section 3: Workflow Metrics
    lines.append("## 3. Workflow Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    if wp:
        lines.append(f"| Populations | {', '.join(wp.populations)} |")
        lines.append(f"| Chromosomes | {', '.join(wp.chromosomes)} |")
        lines.append(f"| Parallelism (J) | {wp.parallelism} |")
    wf = state.workflow_json or {}
    processes = wf.get("processes", [])
    signals = wf.get("signals", [])
    lines.append(f"| Total processes | {len(processes)} |")
    lines.append(f"| Total signals | {len(signals)} |")
    lines.append(f"| K8s jobs completed | {state.task_completion_count} |")
    lines.append(f"| K8s jobs failed | {max(0, state.total_task_count - state.task_completion_count)} |")

    # Task type breakdown
    type_counts: dict[str, int] = {}
    for proc in processes:
        name = proc.get("name", "")
        task_type = name.split("_")[0] if "_" in name else name
        type_counts[task_type] = type_counts.get(task_type, 0) + 1
    for tt, count in sorted(type_counts.items()):
        lines.append(f"| &nbsp;&nbsp;{tt} | {count} |")
    lines.append("")

    # Section 4: Deferred Generation
    lines.append("## 4. Deferred Generation (Experiment 3 data)")
    lines.append("")
    lines.append("### Variant Counts")
    lines.append("")
    lines.append("| Chromosome | Actual (Phase 5) |")
    lines.append("|------------|-------------------|")
    for cd in state.chromosome_data:
        lines.append(f"| chr{cd.chromosome} | {cd.row_count:,} |")
    lines.append("")

    if state.planning_estimates:
        lines.append("### Planning Estimates (Phase 2)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in state.planning_estimates.items():
            if k != "estimated_variants":
                lines.append(f"| {k} | {v} |")
        ev = state.planning_estimates.get("estimated_variants", {})
        for chrom, count in ev.items():
            lines.append(f"| estimated variants chr{chrom} | {count:,} |")
        lines.append("")

    # Section 5: LLM Usage
    lines.append("## 5. LLM Usage")
    lines.append("")
    lines.append("| Metric | Planning (Phase 2) | Monitoring (Phase 9) |")
    lines.append("|--------|-------------------|---------------------|")
    pu = state.llm_usage.get("planning", {})
    mu = state.llm_usage.get("monitoring", {})
    lines.append(f"| Model | {pu.get('model', 'N/A')} | {mu.get('model', 'N/A')} |")
    lines.append(f"| Input tokens | {pu.get('input_tokens', 'N/A')} | {mu.get('input_tokens', 'N/A')} |")
    lines.append(f"| Output tokens | {pu.get('output_tokens', 'N/A')} | {mu.get('output_tokens', 'N/A')} |")
    lines.append(f"| Tool calls | {pu.get('tool_calls', 'N/A')} | {mu.get('api_calls', 'N/A')} |")
    lines.append(f"| Latency (ms) | {pu.get('latency_ms', 'N/A')} | N/A |")
    lines.append("")

    # Section 6: Cluster Utilization
    lines.append("## 6. Cluster Utilization")
    lines.append("")
    if state.cluster_snapshots:
        # Show peak snapshot (last one captured, typically during peak execution)
        peak = state.cluster_snapshots[-1]
        lines.append(f"**Snapshot at:** {peak.get('timestamp', 'N/A')}")
        lines.append(f"**Total snapshots captured:** {len(state.cluster_snapshots)}")
        lines.append("")
        lines.append("```")
        lines.append("# kubectl top nodes")
        lines.append(peak.get("nodes", "N/A"))
        lines.append("")
        lines.append("# kubectl top pods")
        lines.append(peak.get("pods", "N/A"))
        lines.append("```")
    else:
        lines.append("No cluster snapshots captured.")
    lines.append("")

    # Section 7: Raw Artifacts
    lines.append("## 7. Raw Artifacts")
    lines.append("")
    lines.append(f"- Namespace: `{state.namespace}`")
    lines.append(f"- Execution ID: `{state.execution_id}`")
    lines.append("")

    return "\n".join(lines)


def write_experiment_report(
    state: PipelineState,
    output_path: str,
) -> str:
    """Generate and write the experiment report to disk.

    Returns the path where the report was written.
    """
    report = generate_experiment_report(state)

    # Resolve placeholders in path
    path = output_path.replace("{date}", state.execution_id[:8] if state.execution_id else "unknown")

    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(report)

    return path
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_reporting.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/workflow_conductor/reporting.py tests/unit/test_reporting.py
git commit -m "feat: add experiment report generator"
```

---

### Task 8: Call report writer from completion phase

**Files:**
- Modify: `src/workflow_conductor/phases/completion.py:30-85`
- Test: `tests/unit/test_phases.py` (or inline test)

**Step 1: Write the failing test**

```python
class TestCompletionReport:
    async def test_experiment_report_written_when_path_set(
        self, tmp_path: Any
    ) -> None:
        from workflow_conductor.phases.completion import run_completion_phase
        from workflow_conductor.config import ConductorSettings
        from workflow_conductor.models import PipelineState, PipelineStatus, WorkflowPlan

        report_path = str(tmp_path / "report.md")
        settings = ConductorSettings()
        settings.experiment_report_path = report_path
        settings.no_teardown = True

        state = PipelineState(
            user_prompt="test query",
            execution_id="20260312-140000",
            status=PipelineStatus.IN_PROGRESS,
            workflow_status="completed",
            namespace="test-ns",
            phase_timings={"routing": 1.0, "planning": 2.0},
        )
        state.workflow_plan = WorkflowPlan(chromosomes=["17"], populations=["GBR"])

        state = await run_completion_phase(state, settings)
        assert (tmp_path / "report.md").exists()
        content = (tmp_path / "report.md").read_text()
        assert "test query" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_phases.py::TestCompletionReport -v` (or whichever file you placed it in)
Expected: FAIL — completion phase doesn't write a report

**Step 3: Write minimal implementation**

In `completion.py`, add import and report writing after `display_completion_summary(state)`:

```python
from workflow_conductor.reporting import write_experiment_report

# ... (existing code) ...

    display_completion_summary(state)

    # Write experiment report if path configured
    if settings.experiment_report_path:
        try:
            path = write_experiment_report(state, settings.experiment_report_path)
            logger.info("Experiment report written to: %s", path)
        except Exception:
            logger.warning("Failed to write experiment report", exc_info=True)

    return state
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_phases.py::TestCompletionReport -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run make ci`
Expected: PASS

**Step 6: Commit**

```bash
git add src/workflow_conductor/phases/completion.py tests/unit/
git commit -m "feat: write experiment report from completion phase"
```

---

### Task 9: Final integration — run `make ci` and verify

**Step 1:** Run the full CI check:

```bash
make ci
```

Expected: All lint, typecheck, and unit tests pass.

**Step 2:** Check for any mypy issues with new fields:

```bash
make typecheck
```

**Step 3:** Commit any fixes if needed.

---

### Task 10: Update VM `.env` with report path and rsync

**Step 1:** Add report path to VM `.env`:

```bash
SSH_AUTH_SOCK=/tmp/ssh-persistent-agent.sock ssh alice-k8s-dev-node-4 \
  'echo "HF_CONDUCTOR_EXPERIMENT_REPORT_PATH=/home/ubuntu/experiment-reports/e2e-results-{date}.md" >> ~/hyperflow-conductor/.env'
```

**Step 2:** Rsync updated code to VM:

```bash
SSH_AUTH_SOCK=/tmp/ssh-persistent-agent.sock rsync -avz \
  --exclude '.venv' --exclude '__pycache__' --exclude '.mypy_cache' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.git' \
  --exclude '.env' \
  /Users/orzech/Dropbox/home/repos/hyperflow/1000genome/hyperflow-conductor/ \
  alice-k8s-dev-node-4:~/hyperflow-conductor/
```

**Step 3:** Commit if any final changes needed.

```bash
git add -A
git commit -m "chore: finalize experiment data capture for paper runs"
```
