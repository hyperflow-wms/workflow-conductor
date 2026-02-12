# Workflow Conductor

AI-powered orchestrator that accepts **natural language genomics research prompts** and orchestrates the [1000 Genomes](https://www.internationalgenome.org/) analysis workflow on Kubernetes via [HyperFlow WMS](https://hyperflow.2.vu.nl/).

Built on the [mcp-agent](https://github.com/lastmile-ai/mcp-agent) framework, the Conductor coordinates four external components:

| Component | Role |
|---|---|
| **Workflow Composer** | MCP server — generates HyperFlow DAGs from research intent |
| **Workflow Profiler** | LangGraph agent — recommends K8s resource allocations |
| **HyperFlow Engine** | Node.js workflow engine — executes DAGs on K8s |
| **K8s Deployment** | Helm charts + Kind — cluster provisioning and lifecycle |

## Pipeline

```
NL Prompt → Routing → Planning → Validation → Deployment → Monitoring → Completion
               │          │           │             │            │            │
           classify    Composer     Rich UI     Kind+Helm     poll K8s    teardown+
           intent      MCP tools   approve/    10-step        job status  summary
                       via Agent   refine       deploy seq
```

## Prerequisites

- **Docker Desktop** (macOS/Windows) or **Docker Engine** (Linux) — required for Kind clusters
- **Devbox** — cross-platform dev environment manager:
  ```bash
  curl -fsSL https://get.jetify.com/devbox | bash
  ```

## Quick Start

```bash
# 1. Enter dev environment (provides python 3.11, uv, kind, helm, kubectl)
devbox shell

# 2. Install Python dependencies
make install

# 3. Run in dry-run mode (no K8s cluster needed)
make run-dry

# 4. Full run (requires Docker running)
make setup    # Creates Kind cluster, loads images, deploys infra
make run      # Runs conductor with example prompt
```

## Development

All commands assume you're inside `devbox shell`.

```bash
# Code quality
make lint                 # ruff check + format check
make format               # Auto-format
make typecheck            # mypy
make ci                   # lint + typecheck + unit tests

# Testing
make test                 # Unit tests
make test-integration     # Integration tests (requires Kind cluster)
make test-e2e             # End-to-end tests (requires cluster + images)

# Run single test
uv run pytest tests/unit/test_models.py -v
uv run pytest tests/unit/test_models.py::test_function -v

# K8s cluster management
make cluster-create       # 3-node Kind cluster (hfmaster + 2x hfworker)
make cluster-status       # Show cluster/pod/helm status
make cluster-delete       # Tear down cluster

# Run with custom prompt
make run-query Q="Analyze EUR population, chromosome 22, small parallelism."

# Cleanup
make teardown             # Remove Helm releases (keep cluster)
make teardown-all         # Full cleanup (cluster + releases)
```

## Configuration

Copy `.env.example` to `.env` and set your API key:

```bash
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY=sk-ant-...
```

Override settings via environment variables with `HF_CONDUCTOR_` prefix:

```bash
HF_CONDUCTOR_AUTO_APPROVE=true
HF_CONDUCTOR_KUBERNETES__CLUSTER_NAME=my-cluster
HF_CONDUCTOR_LLM__DEFAULT_PROVIDER=google
```

## Project Structure

```
src/workflow_conductor/
├── __init__.py          # Package version
├── cli.py               # Click CLI entry point
├── app.py               # MCPApp + main pipeline
├── config.py            # pydantic-settings configuration
├── models.py            # PipelineState + all data models
├── phases/              # One file per pipeline phase
├── k8s/                 # Async helm/kubectl wrappers
└── ui/                  # Rich terminal UI
```

## License

TBD
