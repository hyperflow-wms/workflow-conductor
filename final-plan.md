# HyperFlow Conductor — Implementation Plan (Final v7)

> **Generated:** 2026-02-12
> **Status:** Final merge of v6 final plan + 4-plan debate merged plan, with user-approved decisions on every dimension
> **Target:** AI-powered orchestrator that accepts natural language genomics research prompts and orchestrates the 1000 Genomes analysis workflow on Kubernetes via HyperFlow WMS.

---

## Provenance: Source Comparison

This plan merges two source plans. The table below shows the origin of each decision.

| Dimension | Winner | Source | User Override? |
|---|---|---|---|
| **MVP framework** | v6 | mcp-agent from day 1 | Yes |
| **MVP phase count** | v6 | 6 phases for MVP | Yes — confirmed |
| **Stage 0 bootstrap** | Merged | Explicit Stage 0 before MVP | Yes |
| **Total stages** | v6+Stage0 | 8 stages (0-7): v6's 7 stages + Stage 0 prepended | Yes |
| **Package layout** | v6 | `src/hyperflow_conductor/` with `phases/`, `k8s/`, `ui/` subpackages | Yes |
| **Code depth** | User decision | Conceptual only (signatures + patterns, not full implementations) | Yes |
| **Data models** | v6 | Rich PipelineState with `synthesize_context_for_composer()`, v2 hooks | Yes |
| **Configuration** | v6 | Nested pydantic-settings, `HF_CONDUCTOR_` prefix, `__` delimiter | Yes |
| **Workflow delivery** | v6 | ConfigMap mount (not kubectl cp) | Yes — confirmed |
| **CLI name** | v6 | `hyperflow-conductor` | Yes — confirmed |
| **Python tooling** | v6 | uv | Yes |
| **PR strategy** | v6 | 7 PRs with branch names, file lists, review focus | Yes |
| **CI pipeline** | v6 | uv + Kind (helm/kind-action) | Yes |
| **Risk analysis** | v6 | 14 risks with Impact+Likelihood (5-column table) | Yes |
| **HyperFlow internals** | Combined | v6's reference section + merged plan's REST API, task execution flow, worker pool mode | Yes |
| **Academic references** | Combined | Union of both sets (~15 papers) | Yes |
| **Appendices** | Merged | All 4: Stage Dependency Map, MCP Tool Reference, Helm Chart Tree, Glossary | Yes |
| **Knowledge system** | Merged | Full 3-tier architecture (universal/workflow/environment) | Yes |
| **Deploy sequence** | Hybrid | v6's 10 steps with bash examples (not full Python) | Yes |
| **Testing** | Combined | Merged CLI options (--keep-cluster, --skip-k8s) + v6 fixture structure | Yes |
| **Makefile** | Combined | Union of unique targets from both plans | Yes |
| **Monitoring** | Combined | Merged Sentinel 3-level architecture + v6 Rich UI + Ctrl+C | Yes |
| **Temporal** | v6 | @app.workflow decorator, signals for human-in-the-loop | Yes |
| **kagent / knowledge order** | v6 | kagent = Stage 6, knowledge = Stage 7 | Yes |
| **Documentation plan** | Merged | File list, update triggers, CLAUDE.md template | Yes |
| **Worker pool docs** | Merged | RabbitMQ + KEDA + persistent workers, ~20% improvement | Yes |
| **K8s comparison table** | Skip | Just state Kind chosen, no comparison table | Yes |
| **Provenance table** | Yes | Include at top of plan | Yes |

---

## Table of Contents

1. [Context & Architecture Decisions](#1-context--architecture-decisions)
2. [Project Structure](#2-project-structure)
3. [Key Models (models.py)](#3-key-models-modelspy)
4. [Pipeline Flow (MVP)](#4-pipeline-flow-mvp)
5. [Deploy Phase Details](#5-deploy-phase-details)
6. [mcp-agent Integration Patterns](#6-mcp-agent-integration-patterns)
7. [Configuration (config.py)](#7-configuration-configpy)
8. [Makefile](#8-makefile)
9. [Testing Plan](#9-testing-plan)
10. [Implementation Stages](#10-implementation-stages)
11. [PR Breakdown for Stage 1](#11-pr-breakdown-for-stage-1)
12. [Verification Plan](#12-verification-plan)
13. [Risks & Mitigations](#13-risks--mitigations)
14. [Documentation Plan](#14-documentation-plan)
15. [Reference Documentation](#15-reference-documentation)

---

## 1. Context & Architecture Decisions

### System Overview

The HyperFlow Conductor is the "brain" that ties together four existing components:

| Component | Role | Interface | Location |
|---|---|---|---|
| **Workflow Composer** | NL -> structured params -> workflow.json | MCP server (5 tools) | `1000genome-workflow/workflow-composer/` |
| **Workflow Profiler** | DAG analysis -> K8s resource recommendations | LangGraph agent (8-phase) | `workflow-profiler/` |
| **HyperFlow WMS** | Node.js workflow engine, Redis state, K8s job executor | REST API + CLI (`hflow run`) | `hyperflow/` |
| **K8s Deployment** | Helm charts (hf-ops + hf-run) for HyperFlow on K8s | Helm CLI + kubectl | `hyperflow-k8s-deployment/` |

The Conductor orchestrates these components through a pipeline, translating a natural language prompt like *"Analyze frequency of genetic variants across European and East Asian populations for chromosomes 1 through 5. Use moderate parallelism."* into a fully deployed and monitored K8s workflow execution.

```
User (natural language)
    |
    v
+--------------------------+
|  HyperFlow Conductor     |  <-- THIS PROJECT (mcp-agent pipeline)
|  (outside K8s for MVP)   |
+----+------+------+-------+
     |      |      |
     v      v      v
  Workflow  Workflow   K8s Cluster
  Composer  Profiler   (HyperFlow + workers)
  (MCP)    (LangGraph)  (Helm charts)
```

### Architecture Decisions Summary

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Orchestration framework | **mcp-agent** (LastMile AI) v0.2.6 | Native MCP support, model-agnostic AugmentedLLM, Temporal integration via config flip |
| 2 | Durability | **asyncio first**, Temporal later | Start simple; `execution_engine: temporal` in config enables migration with zero code changes |
| 3 | LLM providers | **Anthropic Claude + Google Gemini** | Configurable via mcp-agent settings; factory pattern supports provider switching |
| 4 | K8s tooling | **subprocess helm/kubectl** for MVP | No mature Python Helm library; matches `fast-test.sh` exactly; kagent-tool-server for later stages |
| 5 | Runtime location | **Outside K8s** (developer machine) | Simplifies MVP debugging; no pod-level networking complexity |
| 6 | Workflow delivery | **ConfigMap mount** | Survives pod restarts, unlike `kubectl cp`; matches Helm chart patterns |
| 7 | Package layout | `src/hyperflow_conductor/` | Standard Python src layout (PEP 517/621) |
| 8 | Config | **pydantic-settings** | Type-safe config with env var override and `.env` file support |
| 9 | CLI | **Click + Rich** | Rich terminal UI for plan display, progress bars, and user prompts |
| 10 | Statefulness | **Hybrid** — Conductor stateful, Composer stateless | Conductor accumulates artifacts across phases; Composer receives synthesized context per call |
| 11 | Context replay | **Serialize conversation history** in PipelineState | `planner_history` field enables Temporal activity boundary crossing |
| 12 | Test K8s provider | **Kind** | Already used by HyperFlow project; multi-node support; `pytest-kubernetes` supports both Kind and k3d |
| 13 | Test K8s assertions | **kr8s** (async Python K8s client) | Ergonomic async assertions in tests; production code uses subprocess helm/kubectl |
| 14 | Data models | **Pydantic BaseModel** | Type-safe, JSON-serializable via `.model_dump()`, Temporal-compatible, validation built in |
| 15 | CLI name | **hyperflow-conductor** | Descriptive, matches project repo name |
| 16 | Dev environment | **Devbox** (jetify-com/devbox) | Nix-backed reproducible env; pins kind, helm, kubectl, uv, python 3.11; `devbox shell` gives every developer a ready environment; Docker daemon installed separately at OS level |

### Academic Grounding

This work builds on established research in scientific workflow management on Kubernetes. Full references in [§15](#15-reference-documentation).

- Balis (2016) — Foundational HyperFlow paper: process network model, JSON DAG format
- Orzechowski et al. (2024) — Job-based vs. worker-pool execution; worker pools achieve ~20% better makespan
- Balis et al. (2023) — ML-based job execution time prediction using HyperFlow traces from 263 workflow runs
- Alvarenga et al. (2025) — Optimizing resource estimation for scientific workflows (parallels our Profiler)
- Shahin et al. (2025) — Validates agentic workflow patterns for scientific applications

---

## 2. Project Structure

```
hyperflow-conductor/
├── src/
│   └── hyperflow_conductor/
│       ├── __init__.py
│       ├── app.py                    # MCPApp initialization, main pipeline
│       ├── cli.py                    # Click CLI entry point
│       ├── config.py                 # pydantic-settings: ConductorSettings
│       ├── models.py                 # PipelineState, PhaseResult, all data contracts
│       │
│       ├── phases/
│       │   ├── __init__.py
│       │   ├── routing.py            # Phase 1: Intent classification (MVP: hardcoded)
│       │   ├── planning.py           # Phase 2: Workflow planning via Composer MCP
│       │   ├── validation.py         # Phase 3: User validation gate (Rich prompts)
│       │   ├── deployment.py         # Phase 4: Deploy & execute on K8s
│       │   ├── monitoring.py         # Phase 5: Execution monitoring
│       │   └── completion.py         # Phase 6: Teardown & reporting
│       │
│       ├── k8s/
│       │   ├── __init__.py
│       │   ├── helm.py               # Async Helm wrapper (subprocess)
│       │   ├── kubectl.py            # Async kubectl wrapper (subprocess)
│       │   ├── cluster.py            # Kind cluster management
│       │   └── values.py             # Helm values generation from resource profiles
│       │
│       └── ui/
│           ├── __init__.py
│           ├── display.py            # Rich panels, tables, progress bars
│           └── prompts.py            # Rich confirmation prompts
│
├── tests/
│   ├── conftest.py                   # Shared fixtures (mock factories)
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_models.py
│   │   ├── test_config.py
│   │   ├── test_helm.py
│   │   ├── test_kubectl.py
│   │   ├── test_helm_values.py
│   │   └── test_phases.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── conftest.py               # Kind cluster fixture (session-scoped)
│   │   ├── test_composer_mcp.py      # Real MCP server interaction
│   │   ├── test_k8s_deploy.py        # Real Kind cluster + Helm install/delete
│   │   └── test_workflow_inject.py   # ConfigMap injection verification
│   └── e2e/
│       ├── __init__.py
│       ├── conftest.py               # Full pipeline fixtures
│       └── test_full_pipeline.py     # NL prompt -> K8s execution
│
├── mcp_agent.config.yaml             # MCP server config (committed)
├── mcp_agent.config.temporal.yaml    # Temporal config (committed)
├── mcp_agent.secrets.yaml.example    # Template for API keys
├── .env.example                      # Environment variable template
├── devbox.json                        # Devbox: pinned dev tools (kind, helm, kubectl, uv, python)
├── devbox.lock                        # Devbox: locked Nix store paths (committed)
├── pyproject.toml                    # Project metadata, dependencies
├── Makefile                          # Primary development interface
├── CLAUDE.md                         # AI assistant instructions
└── .gitignore
```

### Design Rationale

- **`phases/` directory with one module per phase**: Each phase is independently navigable and reduces merge conflicts when multiple developers work on different phases. Phase modules are thin — they call into k8s/ and use models from models.py.
- **6 MVP phase files only**: The MVP implements 6 phases: routing, planning, validation, deployment, monitoring, completion. Additional phases (profiling, provisioning, generation, approval) are added in later stages.
- **`k8s/` subpackage with 4 focused modules**: Clean testable boundary between orchestration logic and infrastructure operations. These modules can be unit-tested with mocked subprocess, integration-tested against Kind, and replaced later (e.g., kagent MCP).
- **All models in one `models.py`**: All data contracts in one file prevents circular imports and is easy to navigate. Models are Pydantic BaseModel with JSON-serializable fields for Temporal compatibility.
- **No `utils/` directory**: Avoid premature abstractions. If timing or logging helpers are needed, add them when needed, not speculatively.
- **`ui/` subpackage**: Isolates terminal display concerns from pipeline logic; makes testing easier (mock the UI layer).

---

## 3. Key Models (models.py)

All models use Pydantic `BaseModel`. No stdlib `@dataclass`. JSON serialization uses `.model_dump()` and `.model_dump_json()`. All timestamps use `datetime.now(UTC)`.

### Enums

```python
class PipelinePhase(str, Enum):
    """Pipeline phases for the MVP 6-phase architecture.
    Additional phases (PROFILING, PROVISIONING, WORKFLOW_GENERATION,
    EXECUTION_APPROVAL) are added in later stages.
    """
    ROUTING = "routing"
    PLANNING = "planning"
    VALIDATION = "validation"
    DEPLOYMENT = "deployment"
    MONITORING = "monitoring"
    COMPLETION = "completion"

class PipelineStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
```

### Phase & Plan Models

```python
class PhaseResult(BaseModel):
    """Outcome of a single phase execution."""
    phase: PipelinePhase
    status: PipelineStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

class WorkflowPlan(BaseModel):
    """Plan from Workflow Composer (Phase 2 output)."""
    chromosomes: list[str] = Field(default_factory=list)
    populations: list[str] = Field(default_factory=list)
    parallelism: int | None = None
    estimated_data_size_gb: float = 0.0
    description: str = ""
    download_commands: list[str] = Field(default_factory=list)
    data_preparation: dict[str, Any] = Field(default_factory=dict)
    raw_plan: dict[str, Any] = Field(default_factory=dict)

class InfrastructureMeasurements(BaseModel):
    """Measurements from infrastructure provisioning (Stage 2+)."""
    namespace: str = ""
    actual_data_sizes: dict[str, int] = Field(default_factory=dict)
    total_data_size_mb: float = 0.0
    available_vcpus: int = 0
    node_count: int = 0
    memory_gb: float = 0.0
    k8s_version: str = ""
    storage_class: str = ""

class ResourceProfile(BaseModel):
    """Per-task-type resource recommendations."""
    task_type: str = ""
    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""
    confidence: float = 0.0
    source: str = ""  # "profiler" or "composer"
```

### Supporting Models

```python
class ExecutionSummary(BaseModel):
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_runtime_seconds: float = 0.0
    output_path: str = ""

class PipelineError(BaseModel):
    phase: str
    error_type: str
    message: str
    timestamp: str = ""
    recoverable: bool = True
    suggested_action: str = ""

class IntentClassification(BaseModel):
    """Structured output from routing phase LLM call."""
    analysis_type: str = ""
    populations: list[str] = Field(default_factory=list)
    chromosomes: list[int] = Field(default_factory=list)
    focus: str = ""

class UserResponse(BaseModel):
    """User's response to a validation gate."""
    action: str  # "approve", "refine", "abort"
    feedback: str = ""
```

### PipelineState (Central State Object)

```python
class PipelineState(BaseModel):
    """Complete state of a single pipeline execution.

    The Conductor's accumulated context — serializable for Temporal
    activity boundaries and crash recovery. All fields are JSON-serializable.
    Designed for serialization from day one (Temporal compatibility).
    """
    model_config = ConfigDict(use_enum_values=False)

    # Identity
    execution_id: str = ""
    created_at: str = ""

    # Current status
    current_phase: PipelinePhase = PipelinePhase.ROUTING
    status: PipelineStatus = PipelineStatus.PENDING

    # User input
    user_prompt: str = ""

    # Conversation history (for context synthesis to stateless agents)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)

    # Phase outputs (accumulated across the pipeline)
    workflow_plan: WorkflowPlan | None = None
    resource_profiles: list[ResourceProfile] = Field(default_factory=list)
    infrastructure: InfrastructureMeasurements | None = None
    workflow_json: dict[str, Any] | None = None
    workflow_json_path: str = ""
    execution_summary: ExecutionSummary | None = None

    # Phase-specific fields
    intent_classification: str = ""
    user_approved_plan: bool = False
    user_modifications: str = ""
    namespace: str = ""
    cluster_ready: bool = False
    helm_release_name: str = ""
    engine_pod_name: str = ""
    workflow_status: str = ""
    task_completion_count: int = 0
    total_task_count: int = 0
    teardown_completed: bool = False
    user_approved_execution: bool = False

    # Phase v2 hooks (populated in later stages)
    resource_profile: dict[str, Any] | None = None      # From profiler (Stage 3)
    cluster_environment: dict[str, Any] | None = None    # From discovery (Stage 7)

    # Audit trail
    phase_results: list[PhaseResult] = Field(default_factory=list)
    planner_history: list[dict[str, Any]] = Field(default_factory=list)
    phase_timings: dict[str, float] = Field(default_factory=dict)
    errors: list[PipelineError] = Field(default_factory=list)

    # --- Key methods ---

    def record_phase(self, result: PhaseResult) -> None:
        """Append a phase result to the audit trail."""
        ...

    def add_conversation(self, role: str, content: str) -> None:
        """Track conversation turns for context synthesis."""
        ...

    def synthesize_context_for_composer(self) -> str:
        """Build a single coherent context string for stateless Composer calls.

        Assembles: original query + previous plan + user feedback from
        validation gates + infrastructure measurements.
        """
        ...

    def to_json(self) -> str:
        """Serialize to JSON string for Temporal or persistence."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> PipelineState:
        """Deserialize from JSON string."""
        return cls.model_validate_json(json_str)
```

---

## 4. Pipeline Flow (MVP)

The MVP implements 6 phases. Phases for profiling, deferred generation, and execution approval are added in later stages.

```
User NL Prompt
     |
     v
+-------------------------------------------------------------------------+
|  PHASE 1: ROUTING                                                       |
|  Conductor classifies domain -> selects Workflow Composer               |
|  (MVP: hardcoded to 1000genome)                                        |
|  Output: IntentClassification                                           |
+-----------------------------+-------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------------+
|  PHASE 2: PLANNING                                                      |
|  Planner Agent calls Composer MCP tools:                                |
|    1. plan_workflow(intent) -> WorkflowPlan                             |
|    2. estimate_variants(regions) -> size estimates                      |
|  Output: WorkflowPlan (plan + planner_history for context replay)       |
+-----------------------------+-------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------------+
|  PHASE 3: VALIDATION                                             <-- HUMAN |
|  Display interpreted plan via Rich UI:                                  |
|    - Analysis type, populations, chromosomes                            |
|    - Estimated task count, runtime, storage                             |
|  User: approve / refine / abort                                         |
|  If refine: synthesize_context_for_composer() -> re-invoke Composer     |
|  Output: user_approved (bool) + modifications                           |
+-----------------------------+-------------------------------------------+
                              | (rejected -> abort)
                              v
+-------------------------------------------------------------------------+
|  PHASE 4: DEPLOYMENT                                                    |
|  1. Ensure Kind cluster exists (or connect to existing)                 |
|  2. Create namespace, install hf-ops, create ResourceQuota              |
|  3. Stage data via hf-data Helm chart                                   |
|  4. Generate workflow.json via Composer MCP (estimate_variants +        |
|     generate_workflow) using estimated counts (MVP)                      |
|  5. Inject workflow.json via ConfigMap                                   |
|  6. helm upgrade --install hf-run with generated values                 |
|  7. Wait for engine pod readiness                                       |
|  Output: helm_release_name, engine_pod_name                             |
+-----------------------------+-------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------------+
|  PHASE 5: MONITORING                                                    |
|  Poll workflow status:                                                  |
|    - kubectl logs -f (engine container)                                 |
|    - Track job completion (K8s Jobs with app=hyperflow label)           |
|    - Detect failures / stuck states                                     |
|  Rich progress bar showing task completion                              |
|  Output: workflow_status, task_completion_count                         |
+-----------------------------+-------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------------+
|  PHASE 6: COMPLETION                                                    |
|  1. Collect execution logs and metrics                                  |
|  2. Report: total time, tasks completed, resource usage                 |
|  3. Optional: helm delete hf-run + hf-ops (teardown)                   |
|  4. Display summary via Rich                                            |
|  Output: execution_time, output_files, teardown_completed               |
+-------------------------------------------------------------------------+
```

---

## 5. Deploy Phase Details

This replicates what `fast-test.sh` does, but programmatically from Python using `asyncio.create_subprocess_exec`.

### HyperFlow K8s Architecture

- HyperFlow engine runs on `hfmaster` node, workers run on `hfworker` nodes
- NFS-provisioned PVC (`nfs`, ReadWriteMany) shared between engine and worker pods at `/work_dir`
- Redis on master node for workflow state management
- Job-based execution: engine renders job-template.yaml with task-specific variables (`${containerName}`, `${cpuRequest}`, `${memRequest}`, `${command}`) and creates K8s Jobs on hfworker nodes

### Deploy Sequence (10 Steps)

**Step 0: Kind Cluster Setup (if needed)**

```bash
# Check if cluster exists
kind get clusters | grep hyperflow-test

# If not, create with 3-node config (control-plane + hfmaster + 2x hfworker)
kind create cluster --name hyperflow-test \
    --config local/kind-config-3n.yaml

# Load images into Kind
kind load docker-image hyperflowwms/hyperflow:latest --name hyperflow-test
kind load docker-image hyperflowwms/1000genome-worker:1.0-je1.3.4 --name hyperflow-test
kind load docker-image hyperflowwms/1000genome-data:1.0 --name hyperflow-test

# Switch context
kubectl config use-context kind-hyperflow-test
```

**Step 1: Create Workflow Namespace**

```bash
kubectl create namespace {namespace}
# e.g., namespace = "wf-1000g-20260212-abc123"
```

**Step 2: Install hyperflow-ops (infrastructure)**

```bash
helm upgrade --install hf-ops charts/hyperflow-ops \
    --namespace {namespace} \
    --dependency-update \
    --wait --timeout 15m \
    -f local/values-fast-test-ops.yaml
```

This installs: NFS server provisioner (emptyDir for local), RabbitMQ (message broker), KEDA (autoscaler), Worker Pool Operator (CRD + controller). All ops components use `nodeSelector: hyperflow-wms/nodepool: hfmaster`.

**Step 3: Create ResourceQuota (CRITICAL)**

```bash
kubectl create -n {namespace} quota hflow-requests \
    --hard=requests.cpu={resource_quota_cpu},requests.memory={resource_quota_memory}
```

Required by Worker Pool Operator — it checks quota to determine scaling limits. Without this, worker pool autoscaling will not function.

**Step 4: Stage data**

```bash
helm upgrade --install hf-data charts/hyperflow-nfs-data \
    --namespace {namespace}
```

Uses `hyperflowwms/1000genome-data` image which copies VCF files, annotations, and population files to NFS PVC.

**Step 5: Wait for data staging completion**

```bash
kubectl wait --for=condition=complete job/hf-data \
    --namespace {namespace} --timeout=300s
```

**Step 6: Clean up previous hf-run (if exists)**

```bash
helm uninstall hf-run --namespace {namespace} 2>/dev/null || true
kubectl wait --for=delete pod -l component=hyperflow-engine \
    --namespace {namespace} --timeout=60s 2>/dev/null || true
```

**Step 7: Create ConfigMap with generated workflow.json**

```bash
kubectl create configmap workflow-json \
    --namespace {namespace} \
    --from-file=workflow.json={workflow_json_path} \
    --dry-run=client -o yaml | kubectl apply -f -
```

Uses dry-run + apply pattern for idempotent creation.

**Step 8: Install hyperflow-run**

```bash
helm upgrade --install hf-run charts/hyperflow-run \
    --namespace {namespace} \
    --dependency-update \
    -f {base_values} \
    -f {generated_values} \
    --set hyperflow-engine.containers.hyperflow.image={hf_engine_image} \
    --set hyperflow-engine.containers.worker.image={worker_image} \
    --set hyperflow-nfs-data.workflow.image={data_image} \
    --set hyperflow-engine.containers.hyperflow.autoRun=true \
    --wait --timeout 10m
```

**Step 9: Wait for engine pod readiness**

```bash
kubectl wait -n {namespace} --for=condition=ready \
    pod -l component=hyperflow-engine --timeout=5m
```

Record `engine_pod_name` for monitoring phase.

**Step 10: Hand off to MONITORING phase**

### Workflow.json Delivery via ConfigMap

The generated workflow.json is stored in a Kubernetes ConfigMap and mounted into the HyperFlow engine pod at `/work_dir/workflow.json`. This is more K8s-native than `kubectl cp` and survives pod restarts. The `generate_helm_values()` function adds the appropriate volume + volumeMount configuration to the Helm values.

### Helm Chart Paths

Relative to hyperflow-k8s-deployment repo:
- `charts/hyperflow-ops/` — infrastructure (NFS provisioner, Redis template, optional worker-pool operator)
- `charts/hyperflow-run/` — workflow execution (engine deployment, data staging, Redis)
- `charts/hyperflow-engine/` — engine sub-chart (Deployment, ConfigMaps with job-template.yaml)
- `charts/hyperflow-nfs-data/` — data staging Job (copies data image contents to NFS PVC)
- `charts/nfs-volume/` — PVC creation

---

## 6. mcp-agent Integration Patterns

### Programmatic Settings Initialization

Create mcp-agent `Settings` programmatically (not from YAML) for type safety and testability:

```python
# Pattern: Programmatic Settings
from mcp_agent.app import MCPApp
from mcp_agent.config import Settings, MCPSettings, MCPServerSettings, ...

def create_app(settings: ConductorSettings) -> MCPApp:
    mcp_settings = Settings(
        execution_engine="asyncio",
        mcp=MCPSettings(servers={
            "workflow-composer": MCPServerSettings(
                command=settings.workflow.composer_server_command,
                args=settings.workflow.composer_server_args,
            ),
        }),
        anthropic=AnthropicSettings(
            default_model=settings.llm.anthropic_model,
        ) if settings.llm.default_provider == "anthropic" else None,
    )
    return MCPApp(name="hyperflow-conductor", settings=mcp_settings)
```

### Agent for Workflow Composer

```python
# Pattern: Agent + AugmentedLLM for MCP tool calls
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams

COMPOSER_INSTRUCTION = """You are a genomics workflow planning assistant.
Your tools: plan_workflow, generate_workflow, estimate_variants,
list_known_regions, list_populations."""

composer_agent = Agent(
    name="composer",
    instruction=COMPOSER_INSTRUCTION,
    server_names=["workflow-composer"],
)

async with composer_agent:
    llm = await composer_agent.attach_llm(AnthropicAugmentedLLM)
    result = await llm.generate_str(
        message=f"Plan a workflow for: {user_prompt}",
        request_params=RequestParams(max_iterations=5),
    )
```

### LLM Factory Pattern

```python
# Pattern: Multi-provider support (Anthropic + Gemini)
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

LLM_FACTORIES = {
    "anthropic": AnthropicAugmentedLLM,
    "google": GoogleAugmentedLLM,
}

llm_class = LLM_FACTORIES[settings.llm.default_provider]
llm = await agent.attach_llm(llm_class)
```

### Structured Output

```python
# Pattern: Reliable intent extraction via generate_structured
intent = await llm.generate_structured(
    message=f"Classify this research intent: {prompt}",
    response_model=IntentClassification,
)
```

### Context Replay Pattern

```python
# Pattern: Temporal boundary crossing via conversation history serialization
# Phase 2 captures history:
plan_result = await plan_workflow(agent, prompt, provider)
state.planner_history = plan_result["history"]

# Later phase replays history into a fresh agent:
async with generator_agent:
    gen_llm = await generator_agent.attach_llm(llm_class)
    for msg in state.planner_history:
        gen_llm.conversation_history.append(msg)
    workflow = await gen_llm.generate_str("Now generate the workflow.json...")
```

### Main Pipeline Orchestrator

```python
# Pattern: Sequential phase execution with state accumulation
async def run_pipeline(app: MCPApp, settings, user_prompt: str, dry_run=False) -> PipelineState:
    state = PipelineState(
        execution_id=f"wf-1000g-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        created_at=datetime.now(UTC).isoformat(),
        user_prompt=user_prompt,
        namespace=f"wf-1000g-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
    )

    async with app.run():
        # Phase 1: Routing (MVP: hardcoded)
        # Phase 2: Planning (Composer MCP)
        # Phase 3: Validation (Rich prompts, refine loop)
        # if dry_run: return state
        # Phase 4: Deployment (Kind + Helm)
        # Phase 5: Monitoring (poll kubectl)
        # Phase 6: Completion (teardown + summary)

    return state
```

---

## 7. Configuration (config.py)

```python
"""Nested pydantic-settings with HF_CONDUCTOR_ prefix and __ delimiter."""

class KubernetesSettings(BaseSettings):
    cluster_name: str = "hyperflow-test"
    cluster_provider: str = "kind"          # "kind" | "k3d" | "existing"
    kind_config: str = "local/kind-config-3n.yaml"
    kubeconfig: str = ""
    namespace_prefix: str = "wf-1000g"

class HelmSettings(BaseSettings):
    charts_path: str = ""
    ops_values: str = ""
    run_values: str = ""
    timeout_ops: str = "15m"
    timeout_run: str = "10m"

class WorkflowSettings(BaseSettings):
    composer_server_command: str = "python"
    composer_server_args: list[str] = Field(
        default_factory=lambda: ["-m", "workflow_composer.mcp_server"]
    )
    default_parallelism: str = "moderate"

class LLMSettings(BaseSettings):
    default_provider: str = "anthropic"
    anthropic_model: str = "claude-sonnet-4-20250514"
    google_model: str = "gemini-2.0-flash"

class ConductorSettings(BaseSettings):
    """Root configuration. Override via env vars:
        HF_CONDUCTOR_AUTO_APPROVE=true
        HF_CONDUCTOR_KUBERNETES__CLUSTER_NAME=my-cluster
        HF_CONDUCTOR_LLM__DEFAULT_PROVIDER=google
    """
    model_config = SettingsConfigDict(
        env_prefix="HF_CONDUCTOR_",
        env_nested_delimiter="__",
        env_file=".env",
    )

    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)
    helm: HelmSettings = Field(default_factory=HelmSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    # External repo paths
    hyperflow_k8s_deployment_path: str = ""
    workflow_composer_path: str = ""

    # Docker images
    hf_engine_image: str = "hyperflowwms/hyperflow:latest"
    worker_image: str = "hyperflowwms/1000genome-worker:1.0-je1.3.4"
    data_image: str = "hyperflowwms/1000genome-data:1.0"

    # Resource quotas
    resource_quota_cpu: str = "21"
    resource_quota_memory: str = "60Gi"

    # Behavior
    auto_approve: bool = False
    auto_teardown: bool = False
    skip_profiler: bool = True
    verbose: bool = False
    log_level: str = "INFO"
    monitor_poll_interval: int = 10
    monitor_timeout: int = 3600
```

---

## 8. Makefile

```makefile
# HyperFlow Conductor — Development Interface
# =============================================

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Python
UV := uv
SRC := src/hyperflow_conductor
TESTS := tests

# K8s
CLUSTER_NAME ?= hyperflow-test
KIND_CONFIG := local/kind-config-3n.yaml
K8S_DEPLOYMENT_PATH ?= ../../hyperflow-k8s-deployment

# --- Help ---
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

# --- Setup ---
.PHONY: install
install: ## Install project with dev dependencies
	$(UV) sync --all-extras

.PHONY: install-pre-commit
install-pre-commit: ## Install pre-commit hooks
	$(UV) run pre-commit install

# --- Code Quality ---
.PHONY: lint
lint: ## Run linters (ruff)
	$(UV) run ruff check $(SRC) $(TESTS)
	$(UV) run ruff format --check $(SRC) $(TESTS)

.PHONY: format
format: ## Auto-format code
	$(UV) run ruff format $(SRC) $(TESTS)
	$(UV) run ruff check --fix $(SRC) $(TESTS)

.PHONY: typecheck
typecheck: ## Run type checker (mypy)
	$(UV) run mypy $(SRC)

# --- Testing ---
.PHONY: test
test: ## Run unit tests
	$(UV) run pytest $(TESTS)/unit -v

.PHONY: test-integration
test-integration: cluster-ready ## Run integration tests (requires Kind cluster)
	$(UV) run pytest $(TESTS)/integration -v --timeout=300

.PHONY: test-e2e
test-e2e: cluster-ready ## Run E2E tests (requires Kind cluster + images)
	$(UV) run pytest $(TESTS)/e2e -v --timeout=600

.PHONY: test-all
test-all: test test-integration test-e2e ## Run all tests

.PHONY: coverage
coverage: ## Run tests with coverage report
	$(UV) run pytest $(TESTS)/unit --cov=$(SRC) --cov-report=term-missing --cov-report=html

# --- CLI ---
.PHONY: run
run: ## Run conductor with example prompt
	$(UV) run hyperflow-conductor run \
		"Analyze frequency of genetic variants across European and East Asian populations for chromosomes 1 through 5. Use moderate parallelism."

.PHONY: run-dry
run-dry: ## Run conductor in dry-run mode (no K8s deployment)
	$(UV) run hyperflow-conductor run --dry-run \
		"Analyze frequency of genetic variants across European and East Asian populations for chromosomes 1 through 5."

.PHONY: run-query
run-query: ## Run with custom prompt (usage: make run-query Q="...")
	$(UV) run hyperflow-conductor run "$(Q)"

.PHONY: run-debug
run-debug: ## Run conductor in debug mode
	HF_CONDUCTOR_LOG_LEVEL=DEBUG $(UV) run hyperflow-conductor run \
		"Analyze EUR population, chromosome 22, small parallelism."

# --- K8s Cluster ---
.PHONY: cluster-create
cluster-create: ## Create Kind cluster for testing
	@if kind get clusters 2>/dev/null | grep -q "^$(CLUSTER_NAME)$$"; then \
		echo "Cluster '$(CLUSTER_NAME)' already exists"; \
	else \
		kind create cluster --name $(CLUSTER_NAME) --config $(KIND_CONFIG); \
	fi
	kubectl config use-context kind-$(CLUSTER_NAME)

.PHONY: cluster-delete
cluster-delete: ## Delete Kind cluster
	kind delete cluster --name $(CLUSTER_NAME)

.PHONY: cluster-ready
cluster-ready: ## Verify cluster is available
	@kubectl cluster-info > /dev/null 2>&1 || \
		(echo "ERROR: No cluster. Run 'make cluster-create' first." && exit 1)
	@kubectl wait --for=condition=Ready nodes --all --timeout=60s > /dev/null

.PHONY: cluster-status
cluster-status: ## Show cluster and pod status
	@echo "=== Cluster ==="
	kind get clusters
	@echo ""
	@echo "=== Nodes ==="
	kubectl get nodes -o wide 2>/dev/null || true
	@echo ""
	@echo "=== Pods ==="
	kubectl get pods -A 2>/dev/null || true
	@echo ""
	@echo "=== Helm Releases ==="
	helm list 2>/dev/null || true

.PHONY: cluster-load-images
cluster-load-images: cluster-ready ## Load Docker images into Kind cluster
	kind load docker-image hyperflowwms/hyperflow:latest --name $(CLUSTER_NAME)
	kind load docker-image hyperflowwms/1000genome-worker:1.0-je1.3.4 --name $(CLUSTER_NAME)
	kind load docker-image hyperflowwms/1000genome-data:1.0 --name $(CLUSTER_NAME)

# --- Infrastructure ---
.PHONY: infra-up
infra-up: cluster-create ## Deploy hf-ops infrastructure
	helm upgrade --install hf-ops $(K8S_DEPLOYMENT_PATH)/charts/hyperflow-ops \
		--dependency-update --wait --timeout 15m \
		-f $(K8S_DEPLOYMENT_PATH)/local/values-fast-test-ops.yaml

.PHONY: infra-down
infra-down: ## Tear down hf-ops and hf-run
	-helm delete hf-run 2>/dev/null
	-helm delete hf-ops 2>/dev/null

.PHONY: infra-status
infra-status: ## Check infrastructure component status
	@echo "=== Helm Releases ==="
	helm list
	@echo ""
	@echo "=== Infrastructure Pods ==="
	kubectl get pods -l 'app in (nfs-server-provisioner,redis)' -o wide 2>/dev/null || true

# --- Full Setup / Teardown ---
.PHONY: setup
setup: install cluster-create cluster-load-images infra-up ## Full setup from scratch
	@echo "Setup complete. Run 'make run' to start the conductor."

.PHONY: teardown
teardown: infra-down ## Remove Helm releases (keep cluster)

.PHONY: teardown-all
teardown-all: teardown cluster-delete ## Full cleanup (cluster + releases)

# --- Utility ---
.PHONY: logs
logs: ## Tail HyperFlow engine logs
	@POD=$$(kubectl get pods -l component=hyperflow-engine \
		-o jsonpath='{.items[0].metadata.name}' 2>/dev/null); \
	if [ -n "$$POD" ]; then \
		kubectl logs -f "$$POD" -c hyperflow; \
	else \
		echo "No engine pod found"; \
	fi

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: clean-all
clean-all: clean cluster-delete ## Clean everything including cluster

# --- CI ---
.PHONY: ci
ci: lint typecheck test ## Run all CI checks
```

---

## 9. Testing Plan

### Testing Stack

| Tool | Purpose |
|---|---|
| **pytest** | Test runner |
| **pytest-asyncio** | Async test support |
| **pytest-timeout** | Test timeout enforcement |
| **pytest-kind** / **pytest-kubernetes** | K8s cluster fixture (multi-provider: Kind, k3d) |
| **kr8s** | Async Python K8s client for test assertions |
| **unittest.mock** | Mocking agents, LLM calls, subprocess |

### Local K8s Provider: Kind

Kind is chosen because it is already used by the HyperFlow project — `fast-test.sh` and existing CI/CD use Kind, so the `kind-config-3n.yaml` with proper node labels (hfmaster/hfworker) already exists. `pytest-kubernetes` supports both Kind and k3d if a switch is needed later.

### kr8s for Test Assertions

Production code uses subprocess helm/kubectl (proven, matches fast-test.sh). Tests use kr8s for ergonomic async assertions:

```python
import kr8s.asyncio

async def test_hyperflow_engine_running(namespace):
    pods = await kr8s.asyncio.get(
        "pods", namespace=namespace,
        label_selector={"component": "hyperflow-engine"},
    )
    assert len(list(pods)) == 1
    pod = list(pods)[0]
    await pod.wait("condition=Ready", timeout=120)
    assert pod.status.phase == "Running"

async def test_workflow_json_mounted(namespace):
    from kr8s.asyncio.objects import ConfigMap
    cm = await ConfigMap.get("workflow-config", namespace=namespace)
    assert "workflow.json" in cm.data
    loaded = json.loads(cm.data["workflow.json"])
    assert "processes" in loaded

async def test_worker_jobs_complete(namespace):
    jobs = await kr8s.asyncio.get(
        "jobs", namespace=namespace,
        label_selector={"app": "hyperflow"},
    )
    for job in jobs:
        await job.wait(["condition=Complete", "condition=Failed"], timeout=300)
```

### Test Fixtures

```python
"""tests/conftest.py — shared fixtures with CLI options."""

def pytest_addoption(parser):
    parser.addoption("--keep-cluster", action="store_true", default=False)
    parser.addoption("--skip-k8s", action="store_true", default=False)

@pytest.fixture
def settings():
    return ConductorSettings(
        kubernetes={"cluster_name": "test-cluster"},
        helm={"charts_path": "/tmp/charts"},
        auto_approve=True,
        auto_teardown=True,
    )

@pytest.fixture
def sample_plan():
    return WorkflowPlan(
        chromosomes=["1", "2", "3", "4", "5"],
        populations=["EUR", "EAS"],
        parallelism=10,
        estimated_data_size_gb=5.3,
        description="Analyze genetic variants across EUR and EAS for chr1-5.",
    )

@pytest.fixture
def mock_helm():
    helm = AsyncMock()
    helm.release_exists.return_value = False
    helm.upgrade_install.return_value = None
    return helm

@pytest.fixture
def mock_kubectl():
    kubectl = AsyncMock()
    kubectl.wait_for_pod.return_value = "hyperflow-engine-abc123"
    kubectl.create_namespace.return_value = None
    return kubectl
```

```python
"""tests/integration/conftest.py — Kind cluster fixtures."""

@pytest.fixture(scope="session")
def kind_cluster(request):
    if request.config.getoption("--skip-k8s"):
        pytest.skip("K8s tests disabled via --skip-k8s")

    result = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True)
    cluster_exists = KIND_CLUSTER in result.stdout.splitlines()
    if not cluster_exists:
        subprocess.run(["kind", "create", "cluster",
                        "--name", KIND_CLUSTER, "--config", KIND_CONFIG], check=True)

    subprocess.run(["kubectl", "config", "use-context", f"kind-{KIND_CLUSTER}"], check=True)
    yield KIND_CLUSTER
    # Don't delete cluster after tests — reuse across runs for speed
    # To clean up: kind delete cluster --name conductor-test

@pytest.fixture
def test_namespace(kind_cluster):
    ns = f"test-{uuid.uuid4().hex[:8]}"
    subprocess.run(["kubectl", "create", "namespace", ns], check=True)
    yield ns
    subprocess.run(["kubectl", "delete", "namespace", ns,
                     "--ignore-not-found", "--wait=false"], check=False)
```

### CI Pipeline

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jetify-com/devbox-install-action@v0.12.0
      - run: devbox run make install
      - run: devbox run make lint
      - run: devbox run make typecheck
      - run: devbox run make test

  integration:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - uses: jetify-com/devbox-install-action@v0.12.0
      - run: devbox run make install
      - run: devbox run make test-integration

  e2e:
    runs-on: ubuntu-latest
    needs: integration
    steps:
      - uses: actions/checkout@v4
      - uses: jetify-com/devbox-install-action@v0.12.0
      - run: devbox run make install
      - run: devbox run make cluster-create
      - run: devbox run make cluster-load-images
      - run: devbox run make test-e2e
```

> **Note:** Devbox provides kind, helm, kubectl, uv, and python — no separate setup actions needed. Docker is pre-installed on GitHub Actions runners. The `devbox run` prefix executes commands inside the Devbox shell environment.

---

## 10. Implementation Stages

### Stage 0: Project Bootstrap & Infrastructure Validation

**Goal:** Set up project skeleton, tooling, and verify all external dependencies work before writing any pipeline code.

**Scope:**
- Set up Devbox environment: `devbox.json` pinning python 3.11, uv, kind, helm, kubectl; `devbox.lock` committed
- Initialize Python project: `pyproject.toml` with dependencies, `ruff`, `mypy`
- Create Makefile (full version from §8)
- Create Kind cluster configuration (adapted from existing `kind-config-3n.yaml`)
- Create K8s values files for hyperflow-ops and hyperflow-run
- Set up Pydantic v2 data models (`models.py`)
- Verify external dependencies:
  - `hyperflowwms/1000genome-mcp:2.0` image accessible, MCP tools respond
  - `hyperflowwms/hyperflow:latest` image starts correctly
  - Worker and data images available
  - Helm charts install on Kind cluster
- Set up pytest-kind fixtures with kr8s integration
- Write infrastructure smoke tests (NFS, Redis, node labels)
- Create `CLAUDE.md` and `README.md`

**Acceptance Criteria:**
1. `devbox shell` activates environment with all required tools (kind, helm, kubectl, uv, python 3.11)
2. `make install` and `make test` succeed inside Devbox shell
3. `make cluster-create` and `make cluster-delete` work
4. Model unit tests pass
5. Infrastructure smoke tests pass on Kind
6. MCP server Docker image responds to tool calls

---

### Stage 1: MVP — End-to-End Pipeline

**Goal:** NL prompt -> plan -> user approval -> deployed workflow on Kind -> monitoring -> completion

**Scope:**
- mcp-agent integration: MCPApp, Agent, AugmentedLLM
- PipelineState and configuration (pydantic-settings)
- Helm/kubectl async subprocess wrappers
- Click CLI with Rich terminal output
- Phase 1 (routing — hardcoded to 1000genome)
- Phase 2 (planning — mcp-agent + Composer MCP)
- Phase 3 (validation — Rich prompts, refine loop with context synthesis)
- Phase 4 (deployment — Kind cluster + namespace + hf-ops + ResourceQuota + ConfigMap injection + hf-run Helm install + workflow.json generation using estimates)
- Phase 5 (monitoring — poll kubectl for pod/job status)
- Phase 6 (completion — display summary + optional teardown)
- Unit tests for models, config, helm/kubectl wrappers
- Integration test: ConfigMap injection + Helm install on Kind

**Skipped in Stage 1 (with explicit annotations in pipeline):**
- Resource profiling — use Composer estimates
- Deferred generation with real measurements — use estimated variant counts
- Separate execution approval gate — auto-approve
- LLM-based routing (hardcoded)
- Temporal support
- Error recovery / retry loops

**Acceptance Criteria:**
1. `make install` succeeds; `make ci` (lint + typecheck + test) passes
2. `make run` launches CLI, shows Rich-formatted plan, user can approve/refine/abort
3. `make run-dry` exits after showing plan (no K8s)
4. Full pipeline deploys to Kind, engine pod starts, basic monitoring shows progress
5. Teardown cleans up all K8s resources
6. `make test` passes all unit tests
7. `make test-integration` passes ConfigMap injection + Helm install test on Kind
8. `--dry-run` flag works correctly

---

### Stage 2: Deferred Generation + Error Handling

**Goal:** Split generation from interpretation, add infrastructure measurements, and basic structured error handling.

**Scope:**
- New phase file: `phases/provisioning.py` — data staging + infrastructure measurement (query vCPUs, memory, actual data sizes on NFS volume)
- New phase file: `phases/generation.py` — deferred workflow generation with actual measurements via Composer MCP (context replay from Phase 2)
- New phase file: `phases/approval.py` — execution approval gate (Gate 2) with Rich UI showing real task counts, memory estimates, estimated runtime
- ConfigMap-based workflow.json injection using real measurements instead of estimates
- Structured error model: `PipelineError` with phase, error_type, recoverable, suggested_action
- Basic retry logic for transient failures (network errors, pod scheduling delays)

**Updated Pipeline Flow:**
```
ROUTING -> PLANNING -> VALIDATION (Gate 1) -> PROVISIONING -> GENERATION -> APPROVAL (Gate 2) -> DEPLOYMENT -> MONITORING -> COMPLETION
```

**Acceptance Criteria:**
1. Pipeline uses actual data sizes to determine parallelism
2. User sees real task count and memory estimates before execution
3. Context replay works: planner_history from Phase 2 injected into deferred generation
4. Each phase reports structured errors; Conductor retries or escalates to user
5. Integration test: generate -> inject -> verify workflow on Kind

---

### Stage 3: Resource Profiling

**Goal:** Integrate the existing Workflow Profiler for evidence-based K8s resource recommendations.

**Scope:**
- New phase file: `phases/profiling.py` — invoke Workflow Profiler as Python library
- Resource recommendation merging: Profiler + Composer estimates (Profiler takes precedence where confidence > 0.7)
- Per-task-type Helm values generation using profiler output
- K8s Job templates use profiler-recommended resource requests/limits

**Acceptance Criteria:**
1. Conductor optionally invokes the Workflow Profiler after plan approval
2. Profiler produces per-task-type CPU/memory recommendations
3. Recommendations merged with Composer's lookup tables
4. Resource requests in worker pods reflect profiler-sourced values
5. Profiler phase adds <30s to pipeline for small workflows

**Risks:**
- Profiler requires GOOGLE_API_KEY (Gemini) and optionally TAVILY_API_KEY
- Profiler runtime ~9 min for complex workflows; can be skipped for repeat runs

---

### Stage 4: Rich Monitoring + Execution Sentinel

**Goal:** Production-quality monitoring with workflow-aware status tracking, anomaly detection, and Rich terminal UI.

**Scope:**
- **Execution Sentinel** with three awareness levels:
  - **Level 1: Scope-aware** — only watch our namespace (kr8s.asyncio.watch)
  - **Level 2: Progress-aware** — track against task inventory and DAG phases
  - **Level 3: Anomaly-aware** — detect OOMKills, stragglers, stuck tasks
- Rich live display: progress bars, task status table, elapsed time
- Task phase detection (individuals -> sifting -> mutation_overlap -> frequency)
- OOMKill detection via pod events
- Job failure detection and reporting
- Periodic NL progress updates via LLM
- Graceful abort (Ctrl+C -> clean teardown)

**Sentinel Architecture:**

```python
class MonitoringContext(BaseModel):
    namespace: str
    workflow_json: dict
    task_inventory: dict    # {task_type: {count, expected_duration_s, memory_limit}}
    dag_phases: list[str]   # ["individuals", "sifting", "mutation_overlap", "frequency"]
    expected_runtime_minutes: float

class ExecutionSentinel:
    async def watch(self, context: MonitoringContext) -> MonitorResult:
        async for event_type, pod in kr8s.asyncio.watch(
            "pods", namespace=context.namespace,
            label_selector={"app": "hyperflow"}
        ):
            # Level 2: track against task inventory
            # Level 3: detect OOMKills, stragglers
            ...
```

**Acceptance Criteria:**
1. Terminal shows live progress: "32/95 tasks completed — sifting phase"
2. OOMKill events detected and reported with memory stats
3. Failed tasks reported with error details and suggested actions
4. Ctrl+C during any phase triggers clean namespace teardown
5. Workflow completion detected via HyperFlow API or pod status

---

### Stage 5: Temporal Integration

**Goal:** Durable execution via Temporal with a config flip.

**Scope:**
- `execution_engine: temporal` in config activates Temporal mode
- Each phase maps to a Temporal activity with automatic retry
- PipelineState serialization for activity boundaries (already JSON-serializable via Pydantic)
- Human-in-the-loop gates as Temporal signals
- `--temporal` CLI flag overrides config

**Architecture:**

Zero-code-change switch: mcp-agent supports `execution_engine: temporal` config flip. Phase functions stay the same.

```python
@app.workflow
async def run_pipeline(prompt: str) -> PipelineState:
    state = PipelineState(run_id=generate_id(), user_prompt=prompt)
    state = await routing(state)         # Temporal activity 1
    state = await planning(state)        # Temporal activity 2
    state = await validation(state)      # Temporal activity 3 (human signal)
    state = await deployment(state)      # Temporal activity 4
    state = await monitoring(state)      # Temporal activity 5
    state = await completion(state)      # Temporal activity 6
    return state
```

**Acceptance Criteria:**
1. Pipeline runs identically with `execution_engine: temporal`
2. Pipeline state survives Conductor process restart
3. Per-phase timing captured in Temporal event history
4. Validation and approval gates work as Temporal signals

---

### Stage 6: kagent-tool-server Integration

**Goal:** Replace subprocess Helm/kubectl calls with kagent-tool-server's production-ready K8s MCP tools.

**Scope:**
- All K8s operations go through kagent-tool-server MCP
- No subprocess calls to kubectl or helm
- Richer cluster introspection (events, resource descriptions, diagnostics)
- Environment fingerprinting uses kagent for 20+ dimensions of cluster data

kagent (v0.7+, CNCF project, Go-based) provides 21 K8s tools + Helm + Prometheus via MCP:

```yaml
mcp:
  servers:
    kagent:
      url: "http://kagent-tools.kagent:8084/mcp"
```

K8s tools: `GetResources`, `DescribeResource`, `GetEvents`, `GetPodLogs`, `GetResourceYAML`, `GetClusterConfiguration`, `ExecuteCommand`, `CreateResource`, `ApplyManifest`, `PatchResource`, `DeleteResource`, `Scale`, etc.

**Acceptance Criteria:**
1. All K8s operations go through kagent-tool-server MCP
2. No subprocess calls to kubectl or helm remain
3. Richer cluster introspection available
4. Environment fingerprinting uses kagent for 20+ dimensions

---

### Stage 7: Knowledge System

**Goal:** Implement tiered knowledge persistence, environment fingerprinting, and post-execution learning.

**Architecture: Three-Tier Knowledge Store**

```
Tier 1: UNIVERSAL (never expires)
  "VCF files contain variant data"
  "1000Genomes has 7 populations: AFR, ALL, AMR, EAS, EUR, GBR, SAS"
  -> knowledge/universal.yaml

Tier 2: WORKFLOW-SPECIFIC (expires: rarely)
  "For chromosomes 1-5 with medium parallelism, 50 jobs/chrom is optimal"
  -> knowledge/workflows/1000genome.yaml

Tier 3: ENVIRONMENT-CLASS (expires: ~6 months)
  "On 4-node cloud cluster with 32 cores, 250 parallel tasks is optimal"
  -> knowledge/environments/<class>.yaml
```

**Scope:**
- Environment fingerprinting (20+ dimensions via kagent from Stage 6)
- Post-execution learning: actual vs. predicted resource usage
- Git-based knowledge persistence (YAML files)
- Environment matching for repeat executions
- `PipelineState` includes `matched_environment_profile` slot

**Environment Fingerprinting:**

```python
async def collect_environment_fingerprint() -> dict:
    """Collect 20+ dimensions of cluster data."""
    nodes = [n async for n in kr8s.asyncio.get("nodes")]
    return {
        "cpu_cores": sum(int(n.status.allocatable["cpu"]) for n in nodes),
        "cpu_architecture": nodes[0].status.nodeInfo.architecture,
        "total_memory_gb": ...,
        "k8s_version": nodes[0].status.nodeInfo.kubeletVersion,
        "container_runtime": ...,
        "node_count": len(nodes),
        "worker_node_count": len([n for n in nodes if is_worker(n)]),
        "storage_classes": [...],
        # ... 12+ more dimensions
    }
```

**LEARN Phase (runs after every execution):**

```python
async def phase_learn(state, result):
    fingerprint = await collect_environment_fingerprint()
    env_class = classify_environment(fingerprint)
    save_environment_profile(env_class, fingerprint, result)
    append_workflow_history("1000genome", state, result)
```

**Acceptance Criteria:**
1. First run captures execution metrics to knowledge file
2. Second run on same cluster uses matched environment profile
3. Knowledge files are git-friendly YAML
4. Provision phase collects 20+ environment dimensions

### Priority Order

```
Stage 0: Bootstrap (project skeleton + dependency validation)       -- Must have
Stage 1: MVP (end-to-end pipeline + validation)                     -- Must have
Stage 2: Deferred generation + error handling                       -- Must have (robust execution)
Stage 3: Resource profiling                                         -- Should have (quality of execution)
Stage 4: Execution Sentinel                                         -- Should have (operational awareness)
Stage 5: Durability + Temporal                                      -- Should have (production readiness)
Stage 6: kagent integration                                         -- Nice to have (cleaner K8s interaction)
Stage 7: Knowledge system                                           -- Nice to have (future-proofing)
```

Stages 0-2 form the core product. Stages 3-5 make it production-worthy. Stages 6-7 are refinements.

---

## 11. PR Breakdown for Stage 1

### PR 1.1: Project Scaffolding
**Branch:** `feat/scaffold`

**Contents:**
- `devbox.json` pinning python 3.11, uv, kind, helm, kubectl + `devbox.lock`
- `pyproject.toml` with dependencies (mcp-agent, click, rich, pydantic-settings, pyyaml, pytest, ruff, mypy)
- `src/hyperflow_conductor/__init__.py`
- `Makefile` (full version from §8)
- `mcp_agent.config.yaml` with Composer MCP server config
- `mcp_agent.secrets.yaml.example`
- `.env.example`, `.gitignore`
- `CLAUDE.md` (project instructions for AI assistants)

**Tests:** `devbox run make install && devbox run make lint` passes
**Review focus:** Devbox config, dependency choices, project layout, config structure
**Size:** ~250 lines

---

### PR 1.2: Models + Config
**Branch:** `feat/models`

**Contents:**
- `src/hyperflow_conductor/config.py` (ConductorSettings with nested settings classes)
- `src/hyperflow_conductor/models.py` (PipelineState, PipelinePhase, PipelineStatus, PhaseResult, WorkflowPlan, InfrastructureMeasurements, ResourceProfile, ExecutionSummary, PipelineError, IntentClassification, UserResponse)
- `tests/conftest.py` (shared fixtures)
- `tests/unit/test_models.py` (defaults, record_phase, JSON serialization, context synthesis, no shared mutable defaults, v2 slots)
- `tests/unit/test_config.py` (env var loading, nested delimiter, defaults)

**Tests:** `make test` passes with model/config unit tests
**Review focus:** Pydantic BaseModel (not dataclass), JSON serialization via model_dump/model_dump_json, synthesize_context_for_composer(), `datetime.now(UTC)` usage
**Size:** ~500 lines

---

### PR 1.3: K8s Wrappers (Helm + kubectl)
**Branch:** `feat/k8s-wrappers`

**Contents:**
- `src/hyperflow_conductor/k8s/__init__.py`
- `src/hyperflow_conductor/k8s/helm.py` (Helm class: _run, upgrade_install, uninstall, release_exists, list_releases)
- `src/hyperflow_conductor/k8s/kubectl.py` (Kubectl class: _run, get_json, apply_json, wait_for_ready, wait_for_delete, wait_for_pod, wait_for_job, create_configmap_from_file, create_namespace, create_resource_quota, delete_namespace, logs)
- `src/hyperflow_conductor/k8s/cluster.py` (Kind cluster management)
- `src/hyperflow_conductor/k8s/values.py` (generate_helm_values)
- `tests/unit/test_helm.py`, `tests/unit/test_kubectl.py`, `tests/unit/test_helm_values.py`

**Tests:** Unit tests mock subprocess, verify correct argument construction
**Review focus:** Subprocess safety, timeout handling, async patterns, idempotent ConfigMap creation
**Size:** ~600 lines

---

### PR 1.4: CLI + Rich Terminal UI
**Branch:** `feat/cli`

**Contents:**
- `src/hyperflow_conductor/cli.py` (Click commands: `run`, `run --dry-run`)
- `src/hyperflow_conductor/ui/__init__.py`
- `src/hyperflow_conductor/ui/display.py` (Rich panels, progress bars, completion summary)
- `src/hyperflow_conductor/ui/prompts.py` (Rich confirmation prompts: approve/refine/abort)

**Tests:** Unit tests for CLI argument parsing, display formatting
**Review focus:** CLI UX, Rich output formatting, validation gate flow
**Size:** ~300 lines

---

### PR 1.5: Planning Phase + MCP Integration
**Branch:** `feat/planning`

**Contents:**
- `src/hyperflow_conductor/app.py` (MCPApp initialization via create_app, pipeline skeleton)
- `src/hyperflow_conductor/phases/__init__.py`
- `src/hyperflow_conductor/phases/routing.py` (Phase 1: hardcoded to 1000genome)
- `src/hyperflow_conductor/phases/planning.py` (Phase 2: Composer MCP + Agent + AugmentedLLM)
- `src/hyperflow_conductor/phases/validation.py` (Phase 3: validation gate + refine loop)
- `tests/unit/test_phases.py` (mocked MCP tool calls)
- `tests/integration/test_composer_mcp.py` (real MCP server)

**Tests:** Unit: mocked MCP returns expected WorkflowPlan. Integration: real Composer MCP responds.
**Review focus:** MCP tool call construction, conversation history capture, LLM factory pattern
**Size:** ~500 lines

---

### PR 1.6: Deployment + Monitoring + Pipeline Wiring
**Branch:** `feat/pipeline`

**Contents:**
- `src/hyperflow_conductor/phases/deployment.py` (Phase 4: full 10-step deploy sequence)
- `src/hyperflow_conductor/phases/monitoring.py` (Phase 5: basic polling + Rich progress)
- `src/hyperflow_conductor/phases/completion.py` (Phase 6: teardown + summary)
- `src/hyperflow_conductor/app.py` (complete pipeline wiring: all 6 phases)
- Update `cli.py` to call full pipeline

**Tests:** Unit: deployment with mocked helm/kubectl. Integration: full routing->planning->validation with auto_approve.
**Review focus:** Deployment mirrors fast-test.sh, ResourceQuota creation, state transitions, monitoring
**Size:** ~700 lines

---

### PR 1.7: Integration + E2E Tests on Kind
**Branch:** `feat/e2e-tests`

**Contents:**
- `tests/integration/conftest.py` (Kind cluster fixture, image loading, namespace isolation)
- `tests/integration/test_k8s_deploy.py` (Helm chart deployment on Kind)
- `tests/integration/test_workflow_inject.py` (ConfigMap injection verification with kr8s)
- `tests/e2e/conftest.py` (full pipeline fixtures)
- `tests/e2e/test_full_pipeline.py` (NL prompt -> K8s execution, auto_approve=True)

**Tests:** `make test-integration` and `make test-e2e` pass on Kind
**Review focus:** kr8s assertion patterns, cluster reuse, test isolation, timeout configuration
**Size:** ~400 lines

---

## 12. Verification Plan

### Per-PR Verification

Each PR must pass before merge:
1. `make lint` — ruff check + format check
2. `make typecheck` — mypy
3. `make test` — all unit tests pass
4. `make test-integration` — integration tests pass (where applicable)
5. Manual CLI smoke test documented in PR description

### Stage 1 Acceptance Verification

```bash
# 0. Enter Devbox environment (provides kind, helm, kubectl, uv, python)
devbox shell

# 1. Install and verify
make install
make ci                                    # lint + typecheck + unit tests

# 2. Dry-run mode
hyperflow-conductor run --dry-run \
  "Analyze frequency of genetic variants across European and East Asian populations \
   for chromosomes 1 through 5. Use moderate parallelism."
# Expected: Rich panel shows plan, user approves, exits cleanly

# 3. Full pipeline (requires Docker running)
make cluster-create
make cluster-load-images
hyperflow-conductor run \
  "Analyze EUR population, chromosome 22, small parallelism."
# Expected: deploys to Kind, engine pod starts, basic monitoring, completion

# 4. Verify K8s state during execution
make cluster-status
kubectl get pods -A
kubectl get configmaps

# 5. Cleanup
make teardown-all
make cluster-status                        # Verify nothing remains
```

---

## 13. Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **mcp-agent breaking changes** (v0.2.x pre-1.0) | High | Medium | Pin version in pyproject.toml; wrap MCPApp/Agent usage in thin adapter layer; phase logic is pure async Python behind thin wrappers |
| 2 | **LLM intent misclassification** | Medium | Medium | Structured output (`generate_structured`) + Phase 3 validation gate; fallback to explicit parameter prompting |
| 3 | **Kind cluster flakiness in CI** | Medium | High | Cache Docker images; retry cluster creation; session-scoped fixtures; use `--wait` flags on all Helm/kubectl ops |
| 4 | **Helm values schema drift** | High | Low | Pin Helm chart versions; version-specific values generation; integration tests catch drift early |
| 5 | **MCP server startup latency** | Low | Medium | Warm up Composer server in Phase 1; keep connection persistent via mcp-agent lifecycle |
| 6 | **Workflow Profiler unavailability** | Medium | Medium | Default resource profiles as fallback; profiling phase is skippable in MVP (`skip_profiler=True`) |
| 7 | **NFS sync race conditions** | High | Medium | Init container waits for workflow.json (already in Helm chart); add ConfigMap verification step after injection |
| 8 | **Context replay fidelity** | Medium | Low | Serialize full message history via Pydantic model_dump; `synthesize_context_for_composer()` tested in unit tests |
| 9 | **Temporal migration complexity** | Medium | Low | mcp-agent's `@app.workflow`/`@app.task` decorators designed for this; PipelineState is JSON-serializable via Pydantic |
| 10 | **1000 Genomes data image size (~1.7GB)** | Low | High | Pre-pull images; use small test datasets (single chromosome) for CI; `cluster-load-images` Makefile target |
| 11 | **Subprocess helm/kubectl fragility** | Medium | Medium | Structured error parsing (HelmError, KubectlError); timeout handling; fallback to kagent in Stage 6 |
| 12 | **LLM rate limits** | Medium | Low | Sequential LLM calls in MVP; add rate limiting wrapper for Stage 5 (Temporal) |
| 13 | **Missing ResourceQuota** | High | Medium | Explicit step in deployment creates quota; `create_resource_quota()` method on Kubectl wrapper; tested in integration |
| 14 | **ConfigMap size limit (1 MiB)** | Low | Low | Large workflows have ~100KB JSON; monitor size; switch to PVC for very large workflows |

---

## 14. Documentation Plan

### Files to Maintain

| File | Purpose | Updated when |
|------|---------|-------------|
| `README.md` | Quick start, architecture, usage | Every stage |
| `docs/architecture.md` | Detailed architecture, components | Architecture changes |
| `docs/deployment.md` | K8s deployment guide, Helm config | Infrastructure changes |
| `docs/development.md` | Developer setup, testing guide | Tool/dependency changes |
| `CLAUDE.md` | Claude Code guidance for this repo | Every stage |

### CLAUDE.md Template

```markdown
# HyperFlow Conductor

## What This Is
AI-powered orchestrator for 1000genome workflows on Kubernetes via HyperFlow WMS.

## Prerequisites
- Docker Desktop (or Docker Engine on Linux) — required for Kind clusters
- Devbox — install via `curl -fsSL https://get.jetify.com/devbox | bash`

## Getting Started
```bash
devbox shell          # Activates environment: kind, helm, kubectl, uv, python 3.11
make setup            # Full setup from scratch (install + cluster + infra)
```

## Key Commands
- `devbox shell` — Enter dev environment (all tools available)
- `make setup` — Full setup from scratch (cluster + infra)
- `make run` — Run conductor with default prompt
- `make test` — Run unit tests
- `make cluster-status` — Check K8s cluster health

## Architecture
6-phase MVP pipeline: Route -> Plan -> Validate -> Deploy -> Monitor -> Complete

## Key Files
- `devbox.json` — Pinned dev tools (kind, helm, kubectl, uv, python)
- `src/hyperflow_conductor/app.py` — Main pipeline + MCPApp
- `src/hyperflow_conductor/phases/` — One file per phase
- `src/hyperflow_conductor/k8s/` — Helm/kubectl wrappers
- `src/hyperflow_conductor/models.py` — Pydantic v2 data models
- `src/hyperflow_conductor/config.py` — pydantic-settings configuration

## Design Decisions (Do Not Revisit)
- Dev environment: Devbox (Nix-backed, pins all CLI tools)
- Orchestration: mcp-agent from day 1
- Data models: Pydantic BaseModel (not dataclass)
- K8s testing: Kind primary
- K8s client: subprocess (MVP) -> kagent (Stage 6)
- Workflow delivery: ConfigMap mount
- Config: pydantic-settings with HF_CONDUCTOR_ prefix
```

---

## 15. Reference Documentation

### Critical External Files

| # | File | Purpose | Location |
|---|---|---|---|
| 1 | `mcp_server.py` | MCP tools (plan_workflow, generate_workflow, estimate_variants, list_known_regions, list_populations) | `1000genome-workflow/workflow-composer/src/workflow_composer/mcp_server.py` |
| 2 | `models.py` | Pydantic contracts (ResearchIntent, WorkflowPlan, DataPreparationPlan) | `1000genome-workflow/workflow-composer/src/workflow_composer/core/models.py` |
| 3 | `generator.py` | HyperFlowGenerator — authoritative workflow.json structure | `1000genome-workflow/workflow-composer/src/workflow_composer/core/generator.py` |
| 4 | `fast-test.sh` | Reference deployment flow (Kind + Helm) to replicate programmatically | `hyperflow-k8s-deployment/local/fast-test.sh` |
| 5 | `values-fast-test-run.yaml` | Helm values for hf-run | `hyperflow-k8s-deployment/local/values-fast-test-run.yaml` |
| 6 | `values-fast-test-ops.yaml` | Helm values for hf-ops | `hyperflow-k8s-deployment/local/values-fast-test-ops.yaml` |
| 7 | `hyperflow-run/values.yaml` | Full hf-run values reference | `hyperflow-k8s-deployment/charts/hyperflow-run/values.yaml` |
| 8 | `hyperflow-engine/values.yaml` | Engine chart values reference | `hyperflow-k8s-deployment/charts/hyperflow-engine/values.yaml` |
| 9 | `cm.yml` | ConfigMap template with job-template.yaml | `hyperflow-k8s-deployment/charts/hyperflow-engine/templates/cm.yml` |
| 10 | `kind-config-3n.yaml` | Kind cluster config (3 nodes) | `hyperflow-k8s-deployment/local/kind-config-3n.yaml` |
| 11 | `conductor.py` | Reference conductor implementation (Monte Carlo) | `agentic-systems/conductor-sample/conductor.py` |
| 12 | `mcp_agent.config.yaml` | mcp-agent config pattern (asyncio) | `agentic-systems/conductor-sample/mcp_agent.config.yaml` |
| 13 | `mcp_agent.config.temporal.yaml` | mcp-agent config pattern (Temporal) | `agentic-systems/conductor-sample/mcp_agent.config.temporal.yaml` |
| 14 | `agent.py` | LangGraph StateGraph with 8-phase pipeline | `workflow-profiler/workflow_profiler/agent.py` |
| 15 | `output.py` | YAML/Markdown resource recommendation output | `workflow-profiler/workflow_profiler/output.py` |

### HyperFlow Engine Internals Reference

#### REST API Endpoints

The HyperFlow server (`hyperflow-server.js`) exposes:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/apps` | List all workflow instances |
| `POST` | `/apps` | Create workflow instance (JSON body or ZIP archive) |
| `GET` | `/apps/:i` | Workflow instance status |
| `POST` | `/apps/:i` | Emit signal to workflow |
| `GET` | `/apps/:i/ins` | List workflow input signals |
| `GET` | `/apps/:i/sigs` | List all signals (consumed/emitted) |
| `GET` | `/apps/:i/sigs/:j` | Specific signal info |

Creating a workflow via REST:
```bash
curl -X POST http://hyperflow-engine:8080/apps \
  -H "Content-Type: application/json" \
  -d @workflow.json
# Returns: HTTP 201 with Location: /apps/1
```

#### workflow.json Format

Key format rules:
- `signals[i].data = [{}]` marks signal as pre-existing input (already on NFS)
- `processes[i].ins` / `outs` are signal IDs (integers, 0-indexed)
- **Signal ID 0 is valid** — the generator uses explicit `in` membership checks (not truthiness). `if signal_id in inputs` (correct) vs `if signal_id` (wrong — would skip signal 0).
- `config.executor` specifies the command and arguments for the job executor
- `config.cpuRequest` and `config.memRequest` are interpolated into the Job template

**Task count formula:** `C * (J + 2 + 2P)` where C=chromosomes, J=parallel jobs/chr, P=populations
**Parallelism presets:** small=10, medium=50, large=250 ind_jobs/chromosome
**Adaptive parallelism:** Target ~25,000 variants per task, bounds 1.5x-5x vCPU count

#### Task Execution Flow

```
1. HyperFlow engine loads workflow.json
2. Creates FSM (Finite State Machine) per process
3. For each ready task (all input signals available):
   a. Composes job message: {executable, args, inputs, outputs, redis_url, taskId}
   b. Pushes job message to Redis list: <taskId>_msg (via RPUSH)
   c. Creates K8s Job from job-template.yaml (string interpolation)
   d. Job pod runs: hflow-job-execute <taskId> <redis_url>
4. Job executor (in worker pod):
   a. Changes to HF_VAR_WORK_DIR (/work_dir on NFS)
   b. Blocks on Redis BRPOP <taskId>_msg
   c. Spawns child process: executable args...
   d. On completion: RPUSH <taskId> "OK"
5. Engine receives "OK" via BLPOP <taskId>
6. Engine emits output signals -> triggers downstream tasks
7. Repeats until all tasks complete
```

#### Worker Pool Mode (Stage 7)

The worker pool model uses persistent worker deployments instead of one K8s Job per task:

```
Engine -> RabbitMQ queue (per task type) -> Worker Pool (auto-scaled by KEDA)
```

**Decision for Conductor v1:** Start with **job-based mode** (simpler, no RabbitMQ/KEDA dependency). Move to worker pools in Stage 7 for better performance (~20% makespan improvement per Orzechowski et al. 2024).

Worker pool configuration:
```yaml
workerPools:
  enabled: true
  pools:
    - name: individuals
      taskType: individuals
      maxReplicaCount: 50
    - name: sifting
      taskType: sifting
      maxReplicaCount: 10
```

Requires: RabbitMQ, KEDA, Prometheus Adapter.

#### Key Environment Variables

**Engine-level (set on HyperFlow engine pod):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `HF_VAR_function` | `k8sCommand` | Task execution function |
| `HF_VAR_WORKER_CONTAINER` | — | Docker image for worker pods |
| `HF_VAR_WORK_DIR` | `/work_dir` | Working directory on NFS |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis connection |
| `HF_VAR_NAMESPACE` | `default` | K8s namespace for jobs |
| `HF_VAR_JOB_TEMPLATE_PATH` | `./job-template.yaml` | Path to job template |
| `HF_VAR_CPU_REQUEST` | `0.7` | CPU request for worker jobs |
| `HF_VAR_MEM_REQUEST` | `500Mi` | Memory request for worker jobs |
| `HF_VAR_DEBUG` | `0` | Debug mode (1=wait, 0=run) |
| `HF_VAR_WAIT_FOR_INPUT_FILES` | `0` | Wait for input files |
| `HF_VAR_NUM_RETRIES` | `1` | Job retry count |
| `NODE_OPTIONS` | `--max-old-space-size=4096` | Node.js heap size |

**Job template variables (interpolated per-task):**

| Variable | Example | Purpose |
|----------|---------|---------|
| `${jobName}` | `123` | Unique job ID |
| `${containerName}` | `hyperflowwms/1000genome-worker:1.0-je1.3.4` | Worker image |
| `${command}` | `hflow-job-execute 123 redis://redis:6379` | Execution command |
| `${workingDirPath}` | `/work_dir` | NFS mount point |
| `${cpuRequest}` | `0.5` | CPU resource request |
| `${memRequest}` | `50Mi` | Memory resource request |
| `${volumePath}` | `/work_dir` | NFS volume mount path |

### Composer MCP Tool Schemas

```python
# plan_workflow tool parameters
{
    "analysis_type": "population_comparison",
    "populations": ["EUR", "EAS"],
    "chromosomes": [1, 2, 3, 4, 5],
    "focus": "variant_frequency",
    "output_format": "summary",
    "compute_environment": "kubernetes"
}

# generate_workflow tool parameters
{
    "chromosome_data": [
        {"vcf_file": "chr1.vcf.gz", "row_count": 6468094, "annotation_file": "chr1_anno.csv"},
    ],
    "populations": ["EUR", "EAS"],
    "parallelism": "moderate",   # small(10) | moderate(50) | large(250)
    "name": "1000genome-eur-eas-chr1-5"
}

# estimate_variants tool parameters
{"chromosome": 1}

# list_populations — no parameters
# list_known_regions — no parameters
```

### mcp-agent Framework Patterns

```python
# Pattern 1: MCPApp + Agent + AugmentedLLM
app = MCPApp(name="hyperflow_conductor", settings=mcp_settings)
async with app.run():
    planner = Agent(name="planner", instruction="...", server_names=["workflow-composer"])
    async with planner:
        llm = await planner.attach_llm(AnthropicAugmentedLLM)
        result = await llm.generate_str(message="...", request_params=RequestParams(model="..."))

# Pattern 2: Structured output
intent = await llm.generate_structured(message="...", response_model=IntentClassification)

# Pattern 3: Context replay
state.planner_history = llm.conversation_history
# Later: gen_llm.conversation_history.extend(state.planner_history)

# Pattern 4: Multi-provider
LLM_FACTORIES = {"anthropic": AnthropicAugmentedLLM, "google": GoogleAugmentedLLM}
```

### Knowledge Base Documents

| Document | Key Content |
|---|---|
| `agentic-wms-pipeline-1000genome.md` | Target pipeline architecture; sequence diagram; error handling table |
| `design-decisions.md` | 11 resolved architectural decisions with rationale |
| `agentic-systems.md` | Prototype conductor patterns; knowledge system tiers; mcp-agent choice rationale |
| `1000genome-workflow.md` | Genomics pipeline stages; task count formula; parallelism presets |
| `workflow-profiler.md` | Profiler 8-phase loop; token frequency analysis; output format |
| `overview.md` | Component integration map; deployment scenarios |

### Dependencies (pyproject.toml)

```toml
[project]
name = "hyperflow-conductor"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp-agent[anthropic,google]>=0.2.6",
    "pydantic-settings>=2.0",
    "click>=8.0",
    "rich>=13.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "ruff>=0.8",
    "mypy>=1.0",
    "pre-commit>=3.0",
]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-timeout>=2.0",
    "pytest-cov>=6.0",
    "kr8s>=0.17",
    "pytest-kind>=24.0",
]

[project.scripts]
hyperflow-conductor = "hyperflow_conductor.cli:main"
```

### Academic Papers

| Paper | Relevance |
|---|---|
| Balis, B. (2016). "HyperFlow: A model of computation, programming approach and enactment engine for complex distributed workflows." *Future Generation Computer Systems*, 55, 147-162. [DOI: 10.1016/j.future.2015.08.015](https://doi.org/10.1016/j.future.2015.08.015) | Foundational HyperFlow paper |
| Orzechowski, M., Balis, B., Slota, R.G., & Kitowski, J. (2020). "Reproducibility of Computational Experiments on Kubernetes-Managed Container Clouds with HyperFlow." *ICCS 2020*, LNCS vol 12137. [DOI: 10.1007/978-3-030-50371-0_16](https://doi.org/10.1007/978-3-030-50371-0_16) | EDO for scientific workflow reproducibility on K8s |
| Orzechowski, M., Balis, B., & Janecki, K. (2024). "Towards cloud-native scientific workflow management." [arXiv: 2408.15445](https://arxiv.org/abs/2408.15445) | Job-based vs. worker-pool execution; ~20% better makespan |
| Orzechowski, M., Balis, B., Pawlik, K., Pawlik, M., & Malawski, M. (2018). "Transparent Deployment of Scientific Workflows across Clouds — Kubernetes Approach." *IEEE/ACM UCC Companion 2018*. [DOI: 10.1109/UCC-Companion.2018.8605743](https://doi.org/10.1109/UCC-Companion.2018.8605743) | Transparent K8s deployment for scientific workflows |
| Balis, B., Lelek, T., Bodera, J., Grabowski, M., & Grigoras, C. (2023). "Improving prediction of computational job execution times with machine learning." *Concurrency and Computation: Practice and Experience*, 36(2). [DOI: 10.1002/cpe.7905](https://doi.org/10.1002/cpe.7905) | ML-based job prediction using HyperFlow traces (263 runs) |
| Malawski, M., Figiela, K., Bubak, M., Deelman, E., & Nabrzyski, J. (2015). "Scheduling Multilevel Deadline-Constrained Scientific Workflows on Clouds." *Scientific Programming*, 2015(1). [DOI: 10.1155/2015/680271](https://doi.org/10.1155/2015/680271) | Multilevel deadline-constrained scheduling |
| Figiela, K. et al. (2018). "Performance evaluation of heterogeneous cloud functions." *Concurrency and Computation: Practice and Experience*, 30(23). [DOI: 10.1002/cpe.4792](https://doi.org/10.1002/cpe.4792) | HyperFlow as "lightweight workflow engine based on Node.js" |
| Shahin, M.H. et al. (2025). "Agents for Change: AI Workflows for QCP and Translational Sciences." *Clinical and Translational Science*, 18(3). [DOI: 10.1111/cts.70188](https://doi.org/10.1111/cts.70188) | Agentic workflows for scientific applications |
| Lin, T., et al. (2025). "Leveraging Large Language Models for Network Security: A Multi-Expert Approach." *Internet Technology Letters*, 8(3). [DOI: 10.1002/itl2.70016](https://doi.org/10.1002/itl2.70016) | LLM-based orchestrator with specialized experts |
| Verma, P. et al. (2023). "A survey on energy-efficient workflow scheduling algorithms in cloud computing." *Software: Practice and Experience*, 54(5). [DOI: 10.1002/spe.3292](https://doi.org/10.1002/spe.3292) | 1000 Genomes workflow; AI/ML techniques for scheduling |
| Alvarenga, L.C., et al. (2025). "Optimizing Resource Estimation for Scientific Workflows in HPC Environments." *Concurrency and Computation: Practice and Experience*, 37(4-5). [DOI: 10.1002/cpe.8381](https://doi.org/10.1002/cpe.8381) | Resource estimation paralleling our Workflow Profiler |
| Jian, Z., et al. (2023). "DRS: A deep reinforcement learning enhanced Kubernetes scheduler." *Software: Practice and Experience*, 54(10). [DOI: 10.1002/spe.3284](https://doi.org/10.1002/spe.3284) | AI-enhanced K8s scheduling: 27% better utilization |
| Eythorsson, E. & Clark, M.P. (2025). "Toward Automated Scientific Discovery: INDRA." *Hydrological Processes*, 39(3). [DOI: 10.1002/hyp.70065](https://doi.org/10.1002/hyp.70065) | Multi-agent LLM for scientific model orchestration |
| N'Takpe, T. et al. (2021). "Data-aware and simulation-driven planning of scientific workflows on IaaS clouds." *Concurrency and Computation: Practice and Experience*, 34(14). [DOI: 10.1002/cpe.6719](https://doi.org/10.1002/cpe.6719) | Data-aware planning; parallels deferred generation |
| Madduri, R.K., et al. (2014). "Experiences building Globus Genomics." *Concurrency and Computation: Practice and Experience*, 26(13). [DOI: 10.1002/cpe.3274](https://doi.org/10.1002/cpe.3274) | Motivates NL-driven orchestration for biomedical labs |

### Research Gap

No existing work combines LLM-based agentic orchestration + scientific WMS + Kubernetes + MCP protocol. This represents a novel contribution at the intersection of: AI agent frameworks, scientific workflow management, cloud-native computing, and population genomics.

---

## Appendix A: Stage Dependency Map

```
Stage 0: Bootstrap
  ├── Repository structure, Makefile, Kind config
  ├── Pydantic v2 models
  ├── pytest-kind + kr8s fixtures
  └── Infrastructure smoke tests
         │
Stage 1: MVP (depends on Stage 0)
  ├── mcp-agent + Composer MCP (plan + generate)
  ├── subprocess kubectl/helm deployer
  ├── Sequential pipeline (6 phases)
  ├── Basic polling monitor
  └── Unit + integration + E2E tests
         │
Stage 2: Deferred Gen + Errors (depends on Stage 1)
  ├── Provisioning + measurement phases
  ├── Deferred generation with real data
  ├── Execution approval gate
  └── Structured error handling + retry
         │
Stage 3: Profiling (depends on Stage 2)
  ├── Workflow Profiler integration
  └── Resource merging logic
         │
    ┌────┴────┐
    │         │
Stage 4      Stage 5 (independent — can parallelize)
Sentinel     Temporal
    │         │
    └────┬────┘
         │
Stage 6: kagent (depends on Stages 4+5)
  ├── kagent-tool-server MCP integration
  └── Environment fingerprinting (20+ dimensions)
         │
Stage 7: Knowledge System (depends on Stage 6)
  ├── Three-tier knowledge store (YAML)
  ├── Environment fingerprinting + matching
  ├── LEARN phase
  └── Worker pool mode
```

**Key insight:** Stages 4 and 5 are independent — they can be developed in parallel.

## Appendix B: MCP Tool Reference (Workflow Composer)

| Tool | Purpose | Key Parameters |
|------|---------|---------------|
| `plan_workflow` | Create advisory plan | `research_question`, `analysis_type`, `populations`, `chromosomes` |
| `generate_workflow` | Generate HyperFlow JSON | `chromosome_data[]`, `populations`, `parallelism` |
| `estimate_variants` | Estimate variant count | `chromosome`, `region` |
| `list_known_regions` | List genomic regions | (none) |
| `list_populations` | List population codes | (none) |

## Appendix C: Helm Chart Dependency Tree

```
hyperflow-ops (Infrastructure — install once per cluster)
├── nfs-server-provisioner          # NFS for shared /work_dir
│   └── Creates StorageClass: nfs-ganesha
│       └── PVC "nfs" (40Gi, ReadWriteMany)
└── (optional) hyperflow-worker-pool-operator  # Stage 7
    ├── rabbitmq
    ├── keda
    └── prometheus-adapter

hyperflow-run (Workflow Execution — install per workflow)
├── redis                          # HyperFlow job queue + signal store
├── nfs-volume                     # PVC creation
├── hyperflow-engine               # Engine deployment
│   ├── ConfigMap: job-template.yaml
│   ├── ServiceAccount + ClusterRole
│   └── Deployment (hfmaster node)
└── hyperflow-nfs-data             # Data provisioning job
    └── Job: copy data from container image to NFS
```

## Appendix D: Glossary

| Term | Definition |
|------|-----------|
| **Conductor** | The AI orchestrator (this project) that coordinates all agents |
| **Workflow Composer** | MCP server that generates 1000genome HyperFlow DAGs |
| **Workflow Profiler** | LangGraph agent that recommends K8s resource allocations |
| **HyperFlow** | Node.js workflow engine that executes DAGs on K8s |
| **Execution Sentinel** | Monitoring component within the Conductor (Stage 4) |
| **DAG** | Directed Acyclic Graph — the workflow structure |
| **MCP** | Model Context Protocol — standard for agent-tool communication |
| **mcp-agent** | LastMile AI framework for orchestrating MCP servers |
| **kagent** | Kubernetes-native AI agent framework with K8s tools (CNCF) |
| **kr8s** | Async Python Kubernetes client library |
| **VCF** | Variant Call Format — standard genomics data format |
| **Parallelism** | Number of sub-tasks per chromosome for the `individuals` stage |
| **ConfigMap** | K8s resource for injecting configuration data into pods |
| **Temporal** | Durable workflow execution engine for crash recovery |
| **pydantic-settings** | Type-safe configuration with env var override |

---

*End of Final Implementation Plan (v7)*
