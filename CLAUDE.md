# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AI-powered orchestrator ("Conductor") that accepts natural language genomics research prompts and orchestrates the 1000 Genomes analysis workflow on Kubernetes via HyperFlow WMS. Built on the mcp-agent framework (LastMile AI), it coordinates four external components: Workflow Composer (MCP server), Workflow Profiler (LangGraph agent), HyperFlow engine (Node.js WMS), and K8s deployment (Helm charts).

## Prerequisites

- Docker Desktop (or Docker Engine on Linux) — required for Kind clusters
- Devbox — `curl -fsSL https://get.jetify.com/devbox | bash`

## Commands

```bash
# Enter dev environment (provides kind, helm, kubectl, uv, python 3.11)
devbox shell

# Setup & install
make install              # uv sync --all-extras
make setup                # Full: install + cluster-create + cluster-load-images + infra-up

# Code quality
make lint                 # ruff check + format check
make format               # Auto-format
make typecheck            # mypy

# Testing
make test                 # Unit tests only
make test-integration     # Integration tests (requires Kind cluster)
make test-e2e             # End-to-end tests (requires Kind cluster + images)
make test-all             # All of the above
make ci                   # lint + typecheck + test

# Run single test
uv run pytest tests/unit/test_models.py -v
uv run pytest tests/unit/test_models.py::test_specific_function -v

# K8s cluster
make cluster-create       # Create Kind cluster (1 worker node, all pods on hfmaster)
make cluster-delete       # Delete Kind cluster
make cluster-status       # Show cluster/pod/helm status
make cluster-load-images  # Load Docker images into Kind

# Run conductor
make run                  # Default prompt
make run-dry              # Dry-run (no K8s deployment)
make run-query Q="..."    # Custom prompt
make demo                 # Interactive demo (pauses between phases)
make demo-test            # Non-interactive demo (no pauses, for CI/debugging)

# Cleanup
make teardown             # Remove Helm releases (keep cluster)
make teardown-all         # Full cleanup (cluster + releases)
```

## Architecture

10-phase pipeline (Stage 2):

```
NL Prompt → ROUTING → PLANNING → VALIDATION (Gate 1) → PROVISIONING → DATA_PREPARATION
              │          │           │                      │                  │
           hardcoded   Composer    Rich UI              Kind+Helm          decompress
           to 1000g    MCP tools   approve/             hf-ops+hf-run     scan VCF
                       via Agent   refine/abort         wait engine+data  row counts

         → GENERATION → APPROVAL (Gate 2) → DEPLOYMENT → MONITORING → COMPLETION
              │              │                    │            │            │
           Composer       Rich UI              kubectl cp   poll K8s    teardown+
           MCP tool       approve/abort        + signal     job status  summary
           workflow.json  real task counts     engine
```

### Key Patterns

- **mcp-agent orchestration**: `MCPApp` + `Agent` + `AugmentedLLM` for MCP tool calls to Workflow Composer
- **Stateful pipeline**: `PipelineState` (Pydantic BaseModel) accumulates artifacts across phases; JSON-serializable for Temporal compatibility
- **Context replay**: `planner_history` serialized in state enables conversation history to cross Temporal activity boundaries
- **Context synthesis**: `synthesize_context_for_composer()` builds coherent context for stateless Composer calls during refinement loops
- **K8s via subprocess**: Async wrappers around helm/kubectl (not Python K8s client); mirrors `fast-test.sh` flow
- **Conductor signal pattern**: Engine command waits for `/work_dir/.conductor-ready`; conductor copies workflow.json via `kubectl cp` then touches the signal file
- **LLM factory**: `{"anthropic": AnthropicAugmentedLLM, "google": GoogleAugmentedLLM}` — provider switchable via config

### External Components

| Component | Interface | Repo path (relative to `hyperflow/`) |
|---|---|---|
| Workflow Composer | MCP server (5 tools) | `1000genome/1000genome-workflow/workflow-composer/` |
| Workflow Profiler | Python library | `1000genome/workflow-profiler/` |
| HyperFlow K8s Deployment | Helm charts + Kind config | `hyperflow-k8s-deployment/` |
| Conductor Sample | Reference mcp-agent patterns | `agentic-systems/conductor-sample/` |

## Design Decisions (Do Not Revisit)

- **Dev environment**: Devbox (Nix-backed, pins kind/helm/kubectl/uv/python)
- **Orchestration**: mcp-agent from day 1 (not LangGraph, not raw API calls)
- **Data models**: Pydantic BaseModel (not dataclass) — `.model_dump()` / `.model_dump_json()` for serialization
- **All timestamps**: `datetime.now(UTC)` (not naive datetimes)
- **K8s ops**: subprocess helm/kubectl for MVP; kagent-tool-server in Stage 6
- **K8s testing**: Kind primary, kr8s for async test assertions
- **Workflow delivery**: Conductor signal pattern — `kubectl cp` + touch `.conductor-ready` (engine waits for signal before running)
- **Config**: pydantic-settings with `HF_CONDUCTOR_` prefix, `__` nested delimiter
- **CLI**: Click + Rich
- **Package layout**: `src/workflow_conductor/` with `phases/`, `k8s/`, `ui/` subpackages
- **All models in one file**: `models.py` — prevents circular imports
- **No utils/ directory**: Avoid premature abstractions

## Configuration

Override via environment variables:
```
HF_CONDUCTOR_AUTO_APPROVE=true
HF_CONDUCTOR_KUBERNETES__CLUSTER_NAME=my-cluster
HF_CONDUCTOR_LLM__DEFAULT_PROVIDER=google
```

## Workflow Conventions

- **After finishing work**: Always commit, create a PR with `@balis` as reviewer
- **After creating a PR**: Run `/code-review:code-review` to self-review the PR before requesting human review
- **After successful code review**: Automatically merge the PR (no need to wait for user confirmation)
- **After merging a PR**: Always create an annotated git tag (e.g., `v0.1.0-models`) so we can return to that project state later

## Testing Conventions

- **After every PR** (before code review): `make ci` (unit tests + lint + typecheck) + `make test-integration` (K8s tests on Kind, no LLM tokens)
- **After every completed stage**: `make test-e2e` (full pipeline with real LLM tokens + Kind cluster)

### Demo Testing (`make demo-test`)

When testing the demo, run `make demo-test` (non-interactive, no pauses) in a background shell and spawn a **monitoring sub-agent** that watches the demo output in a loop:

1. **Launch**: Run `make demo-test` in background via Bash tool
2. **Monitor**: Spawn a sub-agent that periodically tails the output, checking for:
   - Progress: new phase headers appearing (e.g., `[PROVISIONING]`, `[DEPLOYMENT]`)
   - Stalls: no new output for >30 seconds
   - Errors: `failed`, `Error:`, `Traceback`, non-zero exit codes
3. **On failure/stall**: Stop the demo, investigate (check pod status, events, logs), fix the issue, and re-run `make demo-test` — repeat until the demo completes successfully
4. **Debug tools**: `kubectl get pods -A`, `kubectl describe pod <name> -n <ns>`, `kubectl get events -n <ns> --sort-by=.lastTimestamp`, `make cluster-status`

## Implementation Plan

See `docs/implementation-plan.md` for the full plan (8 stages, 7 PRs for Stage 1). Stages 0-2 are core, 3-5 production-worthy, 6-7 refinements.
