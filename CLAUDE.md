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
make cluster-create       # Create Kind cluster (3 nodes: control-plane + hfmaster + 2x hfworker)
make cluster-delete       # Delete Kind cluster
make cluster-status       # Show cluster/pod/helm status
make cluster-load-images  # Load Docker images into Kind

# Run conductor
make run                  # Default prompt
make run-dry              # Dry-run (no K8s deployment)
make run-query Q="..."    # Custom prompt

# Cleanup
make teardown             # Remove Helm releases (keep cluster)
make teardown-all         # Full cleanup (cluster + releases)
```

## Architecture

6-phase MVP pipeline (later stages add more phases):

```
NL Prompt → ROUTING → PLANNING → VALIDATION → DEPLOYMENT → MONITORING → COMPLETION
              │          │           │              │            │            │
           hardcoded   Composer    Rich UI      Kind+Helm    poll K8s    teardown+
           to 1000g    MCP tools   approve/     10-step      job status  summary
                       via Agent   refine/abort  deploy seq
```

### Key Patterns

- **mcp-agent orchestration**: `MCPApp` + `Agent` + `AugmentedLLM` for MCP tool calls to Workflow Composer
- **Stateful pipeline**: `PipelineState` (Pydantic BaseModel) accumulates artifacts across phases; JSON-serializable for Temporal compatibility
- **Context replay**: `planner_history` serialized in state enables conversation history to cross Temporal activity boundaries
- **Context synthesis**: `synthesize_context_for_composer()` builds coherent context for stateless Composer calls during refinement loops
- **K8s via subprocess**: Async wrappers around helm/kubectl (not Python K8s client); mirrors `fast-test.sh` flow
- **ConfigMap workflow delivery**: workflow.json injected via ConfigMap mount (not kubectl cp)
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
- **Workflow delivery**: ConfigMap mount (not kubectl cp)
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
- **After merging a PR**: Always create an annotated git tag (e.g., `v0.1.0-models`) so we can return to that project state later

## Implementation Plan

See `docs/implementation-plan.md` for the full plan (8 stages, 7 PRs for Stage 1). Stages 0-2 are core, 3-5 production-worthy, 6-7 refinements.
