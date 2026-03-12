# E2E Experiment Instructions (Experiment 5)

## Goal

Run 3 queries end-to-end through the Conductor pipeline on a 3-node Kubernetes cluster. Collect phase-level timing, task counts, data volumes, and LLM usage. Also collect manual workflow specification times from 2-3 co-authors for the same queries.

This produces data for **two** paper sections:
- **Experiment 3 (Deferred Generation)**: estimated vs actual variant counts and data transfer savings (extracted from Phase 2 vs Phase 5 data within these runs)
- **Experiment 5 (E2E Demo)**: phase-level timing breakdown and manual comparison

---

## Cluster Setup

**3 nodes**, each ~16 vCPUs, ~56 GB RAM, Ubuntu 24.04.

Before running, verify cluster is ready:
```bash
kubectl get nodes
# Should show 3 nodes in Ready state

kubectl describe nodes | grep -A6 "Allocatable:"
# Record total cluster capacity
```

Record cluster info in the output document.

---

## Queries

Run these 3 queries in order. Between queries, tear down the namespace to start clean.

### Q1 — Multi-region, multi-population (moderate)
```
Compare European, East Asian, and African populations in the HLA and BRCA1 regions.
```
- Expected: chr6 (HLA) + chr17 (BRCA1), populations EUR+EAS+AFR
- Expected tasks: ~340 (2 chromosomes × (small_J + 2 + 2×3))
- Exercises: multi-region vocabulary mapping, tabix extraction for both regions

### Q2 — Multi-chromosome, two populations (medium)
```
Analyze variant patterns across chromosomes 21 and 22 in European and African populations.
```
- Expected: chr21 + chr22, populations EUR+AFR
- Expected tasks: ~580 (2 chromosomes × (medium_J + 2 + 2×2))
- Exercises: whole-chromosome download (no tabix), medium parallelism, deferred generation calibrating J to actual data

### Q3 — Large-scale, all super-populations (stress test)
```
Compare all five super-populations across chromosomes 1 through 5.
```
- Expected: chr1-5, populations EUR+AFR+EAS+SAS+AMR
- Expected tasks: ~3,600 (5 chromosomes × (large_J + 2 + 2×5))
- Exercises: large data volumes, high parallelism, cluster utilization across 3 nodes

---

## Repositories

| Component | Absolute Path |
|-----------|--------------|
| **Conductor** | `/Users/orzech/Dropbox/home/repos/hyperflow/1000genome/hyperflow-conductor/` |
| **1000 Genomes Workflow** | `/Users/orzech/Dropbox/home/repos/hyperflow/1000genome/1000genome-workflow/` |
| **Skills** | `/Users/orzech/Dropbox/home/repos/hyperflow/1000genome/1000genome-workflow/workflow-composer/src/workflow_composer/skills/` |
| **Paper (this repo)** | `/Users/orzech/Dropbox/home/repos/papers/europar-2026-hyperflow-conductor/` |
| **Output documents go here** | `/Users/orzech/Dropbox/home/repos/papers/europar-2026-hyperflow-conductor/research/` |

---

## Data Collection Checklist (per query)

For each query Q1/Q2/Q3, fill in the output template below with these measurements:

### A. Timing Breakdown (seconds)

Measure wall-clock time for each phase. Extract from Conductor log timestamps.

| Phase | What to measure | How |
|-------|----------------|-----|
| Phase 1: Routing | Time from pipeline start to routing complete | Log timestamps |
| Phase 2: Planning | Time for LLM planning call(s) | Log timestamps (look for MCP tool calls) |
| Phase 3: Validation | Skipped (auto-approve) | 0 |
| Phase 4: Provisioning | Helm install + pod ready wait | Log timestamps |
| Phase 5: Data Preparation | Tabix extraction + VCF decompression + row counting | Log timestamps |
| Phase 6: Generation | Time for LLM generation call | Log timestamps |
| Phase 7: Approval | Skipped (auto-approve) | 0 |
| Phase 8: Deployment | kubectl cp + signal | Log timestamps |
| Phase 9: Monitoring | From signal to all jobs complete | Log timestamps (poll intervals) |
| Phase 10: Completion | Teardown + summary | Log timestamps |
| **Total** | End-to-end wall clock | `time` command output |

Group these into higher-level categories for the paper:
- **LLM time** = Phase 2 + Phase 6
- **Infrastructure time** = Phase 4 + Phase 5 + Phase 8
- **Execution time** = Phase 9
- **Overhead** = Phase 1 + Phase 10

### B. Task & Workflow Metrics

| Metric | Value |
|--------|-------|
| Populations interpreted | (list codes) |
| Chromosomes interpreted | (list numbers) |
| Regions interpreted | (list names + coordinates if applicable) |
| Parallelism selected (J) | |
| Total tasks in workflow.json | |
| Total signals in workflow.json | |
| K8s jobs submitted | |
| K8s jobs completed | |
| K8s jobs failed | |

### C. Deferred Generation Data (for Experiment 3)

This data comes from within the same runs. Compare Phase 2 (advisory plan) vs Phase 5+6 (actual measurements).

| Metric | Phase 2 (estimated) | Phase 5/6 (actual) | Delta |
|--------|--------------------|--------------------|-------|
| Variant count, chr X | | | |
| Variant count, chr Y | | | |
| (repeat per chromosome) | | | |
| Task count (total) | | | |
| Parallelism (J per chr) | | | |
| Data downloaded (bytes) | | | |
| Full chromosome size (bytes) | | | |
| Data transfer savings (%) | | | |

For region queries (Q1): record tabix-extracted size vs full chromosome VCF size.
For whole-chromosome queries (Q2): record if/how parallelism (J) changed.

### D. LLM Usage

| Metric | Phase 2 (Planning) | Phase 6 (Generation) | Total |
|--------|-------------------|---------------------|-------|
| Model ID | | | |
| Input tokens | | | |
| Output tokens | | | |
| Tool calls made | | | |
| API latency (ms) | | | |
| Estimated cost ($) | | | |

### E. Cluster Utilization (optional but nice)

Capture a snapshot during peak execution (Phase 9):
```bash
kubectl top nodes
kubectl top pods -n <namespace>
```

---

## Manual Comparison Protocol

For each of the 3 queries, 2-3 co-authors independently specify the equivalent workflow **without** using the Conductor. This measures the human time saved.

### Instructions for Co-Authors

1. You are given a research question (same as Q1/Q2/Q3)
2. You have access to:
   - 1000 Genomes project documentation
   - HyperFlow workflow documentation
   - The `g1kwf` CLI help (`g1kwf --help`, `g1kwf generate --help`)
   - The Skills documents at `/Users/orzech/Dropbox/home/repos/hyperflow/1000genome/1000genome-workflow/workflow-composer/src/workflow_composer/skills/`
   - The domain knowledge file at `/Users/orzech/Dropbox/home/repos/hyperflow/1000genome/hyperflow-conductor/knowledge/1000genome-workflow.md`
3. Your task: produce the correct `g1kwf generate` command (or equivalent workflow.json) that answers the research question
4. Timer starts when you read the question, stops when you have a working command

### What to Record (per co-author, per query)

| Metric | Value |
|--------|-------|
| Co-author ID | (A / B / C) |
| Query | (Q1 / Q2 / Q3) |
| Time to specification (minutes) | |
| Steps taken | (free text: what docs consulted, what lookups performed) |
| Final command / parameters | |
| Correct? (vs ground truth) | (yes / no / partial — specify errors) |
| Errors made | (wrong population code, wrong region coordinates, etc.) |

---

## Output Document Template

Create one document per query run in `/Users/orzech/Dropbox/home/repos/papers/europar-2026-hyperflow-conductor/research/`.

Name: `e2e-results-Q<N>-<YYYYMMDD>.md`

```markdown
# E2E Experiment Results — Q<N>

**Date:** YYYY-MM-DD HH:MM
**Query:** "<exact query text>"
**Cluster:** <N> nodes, <CPUs> vCPUs total, <RAM> GB RAM total
**LLM Model:** <model ID>
**Conductor Version:** <version/commit>
**MCP Server Image:** <image:tag>

## 1. Intent Interpretation

| Field | Expected (ground truth) | Actual (Conductor output) | Match? |
|-------|------------------------|--------------------------|--------|
| analysis_type | | | |
| populations | | | |
| chromosomes | | | |
| regions | | | |
| focus | | | |

## 2. Phase Timing (seconds)

| Phase | Duration (s) | Notes |
|-------|-------------|-------|
| 1. Routing | | |
| 2. Planning (LLM) | | |
| 3. Validation | skipped | auto-approve |
| 4. Provisioning | | |
| 5. Data Preparation | | |
| 6. Generation (LLM) | | |
| 7. Approval | skipped | auto-approve |
| 8. Deployment | | |
| 9. Monitoring (execution) | | |
| 10. Completion | | |
| **Total** | | |

### Grouped

| Category | Duration (s) | % of total |
|----------|-------------|------------|
| LLM (Phase 2+6) | | |
| Infrastructure (Phase 4+5+8) | | |
| Execution (Phase 9) | | |
| Overhead (Phase 1+10) | | |

## 3. Workflow Metrics

| Metric | Value |
|--------|-------|
| Populations | |
| Chromosomes | |
| Regions | |
| Parallelism (J) | |
| Total tasks | |
| Total signals | |
| K8s jobs submitted | |
| K8s jobs completed | |
| K8s jobs failed | |

## 4. Deferred Generation (Experiment 3 data)

### Variant Counts

| Chromosome | Estimated (Phase 2) | Actual (Phase 5) | Delta (%) |
|------------|---------------------|-------------------|-----------|
| | | | |

### Data Transfer

| Chromosome | Full VCF size | Downloaded size | Savings (%) |
|------------|--------------|-----------------|-------------|
| | | | |

### Task Count Adjustment

| Metric | Advisory (Phase 2) | Final (Phase 6) | Delta |
|--------|-------------------|-----------------|-------|
| J (parallelism) | | | |
| Total tasks | | | |

## 5. LLM Usage

| Metric | Planning (Phase 2) | Generation (Phase 6) | Total |
|--------|-------------------|---------------------|-------|
| Model | | | |
| Input tokens | | | |
| Output tokens | | | |
| Tool calls | | | |
| API latency (ms) | | | |
| Cost ($) | | | |

## 6. Manual Comparison

| Metric | Co-author A | Co-author B | Co-author C |
|--------|------------|------------|------------|
| Time (min) | | | |
| Correct? | | | |
| Errors | | | |

## 7. Cluster Snapshot (during peak execution)

```
<paste kubectl top nodes output>
```

## 8. Raw Artifacts

- Log file: `run-Q<N>-<timestamp>.log`
- Workflow JSON: `workflow-Q<N>.json`
- Namespace: `<namespace-name>`

## 9. Notes / Anomalies

<any unexpected behavior, errors, retries, etc.>
```

---

## Execution Order

1. Provision 3-node cluster, verify ready
2. Run Q1 (moderate) — validates setup, shortest run
3. Run Q2 (medium) — good deferred generation data
4. Run Q3 (stress test) — longest, run last
5. Collect manual comparison data from co-authors (can be done in parallel with runs)
6. Fill in all 3 output documents

## Estimated Total Time

| Step | Time |
|------|------|
| Cluster setup + verification | 10-15 min |
| Q1 run + data collection | 15-20 min |
| Q2 run + data collection | 20-30 min |
| Q3 run + data collection | 40-60 min |
| Manual comparison (async) | 30-60 min per co-author |
| **Total (runs only)** | **~1.5-2 hours** |
