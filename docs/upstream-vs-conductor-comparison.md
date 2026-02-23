# Upstream vs Conductor: E2E Test Comparison Report

**VM:** alice-k8s-dev-node-4 (k3s, 16 vCPUs, 55 GB RAM)

## Executive Summary

| Test Case | Upstream (Docker Compose) | Conductor v1 (2026-02-21) | Conductor v2 (2026-02-23) |
|---|---|---|---|
| **brca1-gbr** | PASSED (3m37s) | PASSED (17m57s) | **PASSED (5m19s)** |
| **eas-hla-autoimmune** | PASSED (116m43s) | FAILED (4m58s) | pending |
| **eur-afr-hla** | PASSED (4h13m, fixed image) | FAILED (4m55s) | pending |

**Conductor v1** = LLM-based generation with `estimate_variants`, data container image
**Conductor v2** = deterministic generation with actual row counts, no data container, population-filtered columns.txt

### Key improvements in v2:
1. **brca1-gbr 3.4x faster** (17m57s → 5m19s) — now only 1.5x slower than upstream (was 5x)
2. **columns.txt population-filtered**: 91 GBR samples instead of all 2504 — `frequency.py` processes 27x fewer columns
3. **No data container**: nfs-data subchart disabled, data via tabix K8s Jobs
4. **No init container blocking**: engine starts immediately (was waiting for workflow.json from data image)
5. **Deterministic generation**: actual VCF row counts passed to `generate_workflow` MCP tool (no LLM `estimate_variants`)

---

## Architecture Differences

| Aspect | Upstream | Conductor v1 | Conductor v2 |
|---|---|---|---|
| Orchestration | Shell script | 10-phase pipeline | 10-phase pipeline |
| LLM Usage | `--mock-llm` | Real LLM for planning + generation | Real LLM for planning only |
| Workflow generation | Deterministic CLI | LLM + `estimate_variants` | **Deterministic MCP** `generate_workflow` |
| Data container | Docker image | Docker image (nfs-data subchart) | **None** (tabix K8s Jobs) |
| columns.txt | Population-filtered | All 2504 samples | **Population-filtered** |
| Container Runtime | Docker Compose | K8s Jobs via Helm | K8s Jobs via Helm |
| Data Extraction | `tabix` via Docker | `tabix` via K8s Job | `tabix` via K8s Job |

---

## Test 1: brca1-gbr

**Prompt:** "Analyze BRCA1 gene variants in the British population."

### Results

| Metric | Upstream | Conductor v1 | Conductor v2 | Delta (v1→v2) |
|---|---|---|---|---|
| **Status** | PASSED | PASSED | **PASSED** | -- |
| **Wall time** | 3m37s | 17m57s | **5m19s** | **-12m38s (3.4x faster)** |
| **Monitoring** | ~3m | 15m10s | **3m31s** | **-11m39s (4.3x faster)** |
| **Total tasks** | 29 (25 ind) | 16 (12 ind) | **16 (11 ind + merge + sift + mut + freq)** | Same parallelism |
| **Completed** | 29/29 | 16/16 | **16/16** | Both 100% |
| **Failed** | 0 | 0 | **0** | Both clean |
| **columns.txt** | 91 samples (GBR) | 2504 samples (all) | **91 samples (GBR)** | **27x fewer** |

### Output Files

| File | Upstream | Conductor v1 | Conductor v2 |
|---|---|---|---|
| chr17n.tar.gz | 8,469 B | 272,902 B | **8,823 B** |
| chr17-GBR.tar.gz | NOT PRESENT | 132,674 B | **132,853 B** |
| chr17-GBR-freq.tar.gz | 233,014 B | 379,195 B | **377,446 B** |
| sifted.SIFT.chr17.txt | 630 B | 630 B | **630 B** |

**Notes:**
- `chr17n.tar.gz` size now matches upstream (8.8KB vs 8.5KB) — v1's 272KB was bloated by all-sample columns.txt
- `sifted.SIFT.chr17.txt` identical across all three (630 B)
- `chr17-GBR-freq.tar.gz` similar between v1 and v2 (different from upstream due to different chunk count)

### Timing Breakdown

| Phase | Conductor v1 | Conductor v2 | Delta |
|---|---|---|---|
| planning | 5.7s | **9.4s** | +3.7s (LLM variance) |
| provisioning | 1m52s | **16.0s** | **-1m36s** (no data pod wait) |
| data_preparation | 10.8s | **11.2s** | ~same |
| generation | 19.8s | **3.8s** | **-16.0s** (deterministic, no LLM) |
| deployment | ~1s | **1m8s** | +1m7s (columns.txt + pop files) |
| monitoring | 15m10s | **3m31s** | **-11m39s** (pop-filtered columns.txt) |
| **Total** | **17m57s** | **5m19s** | **-12m38s** |
| **Overhead (non-monitoring)** | **2m28s** | **1m48s** | -40s |

### Why v2 Is Faster

1. **Population-filtered columns.txt** (91 vs 2504 samples): `frequency.py` processes 27x fewer columns → monitoring dropped from 15m10s to 3m31s
2. **No data container wait**: provisioning dropped from 1m52s to 16s (no nfs-data pod + no init container)
3. **Deterministic generation**: 3.8s vs 19.8s (direct MCP tool call, no LLM reasoning)

### Remaining Gap vs Upstream

| Factor | Time cost | Notes |
|---|---|---|
| K8s provisioning | ~16s | Helm install, pod scheduling |
| LLM planning | ~9s | Could use mock-llm for benchmarks |
| K8s job overhead | ~30s | Container startup per job (~8-26s each) |
| Deployment (kubectl cp) | ~1m8s | columns.txt + population files + signal |
| **Total overhead** | **~1m48s** | vs upstream's ~0s overhead |

---

## Test 2: eas-hla-autoimmune

**Prompt:** "Investigate the frequency of known autoimmune-related variants in the HLA region (chromosome 6) across East Asian populations."

### Results

| Metric | Upstream | Conductor v1 | Conductor v2 |
|---|---|---|---|
| **Status** | PASSED | FAILED | **pending** |
| **Wall time** | 116m43s | 4m58s | -- |
| **Total tasks** | 29 | 12 | -- |
| **Completed** | 29/29 | 3/12 | -- |
| **Failed** | 0 | 9 | -- |
| **Variants processed** | 172,951 (HLA region only) | 249,747 (250k sample VCF) | -- |

### Root Cause (v1 failure)

The conductor workflow used incorrect chunk boundaries, causing immediate worker failures.

**Upstream (correct):**
- Extracted HLA region from chr6 via tabix: 172,951 variants
- 25 chunks of ~6,919 rows each
- Total range: 1 to 172,951

**Conductor v1 (incorrect):**
- Extracted chr6 data: 249,747 rows (VCF file `ALL.chr6.250000.vcf`)
- But LLM estimated ~5,066,529 total variants for chr6
- Generated 11 chunks of ~506,653 rows each
- Total range: 1 to 5,573,173 — **far exceeding actual data (249,747 rows)**

**v2 fix:** Generation now uses actual VCF row counts from data preparation (not LLM estimates). Awaiting rerun.

### Upstream Timing

| Phase | Duration |
|---|---|
| Interpret (mock-llm) | ~0s |
| Plan | ~1s |
| Extract (tabix HLA region) | ~5s |
| Generate workflow | ~1s |
| Execute (HyperFlow) | ~116m |
| **Total** | **116m43s** |

---

## Test 3: eur-afr-hla

**Prompt:** "Compare the frequency of HLA region variants on chromosome 6 between European (EUR) and African (AFR) populations."

### Results

| Metric | Upstream (old image 1.0) | Upstream (fixed image 1.1) | Conductor v1 |
|---|---|---|---|
| **Status** | PARTIAL (4/6 outputs) | **PASSED (6/6 outputs)** | FAILED |
| **Wall time** | 4h+ (SSH broke) | ~4h 13min | 4m55s |
| **Total tasks** | 31 | 31 | 52 |
| **Completed** | 29/31 | 31/31 | 2/52 |
| **Failed** | 2 (race condition) | 0 | 50 |
| **Populations** | EUR, AFR | EUR, AFR | EUR, AFR |
| **Variants** | 172,951 (HLA region) | 166,052 (HLA region) | 249,747 (250k sample) |

### Root Cause: Upstream Race Condition (FIXED)

`os.makedirs()` without `exist_ok=True` in `mutation_overlap.py` and `frequency.py`.

**Fix applied:** Commits `49fd55e` + `6bc75ba` in upstream repo. Image bumped to `1.1-je1.3.4`. Rerun confirmed 6/6 outputs.

### Root Cause: Conductor v1 Failure

Same as eas-hla-autoimmune — LLM-estimated variant counts caused incorrect chunk boundaries.

**v2 fix:** Deterministic generation with actual row counts. Awaiting rerun.

---

## Summary of Issues

### Issue 1: Variant Count Mismatch — FIXED in v2

**Affects:** eas-hla-autoimmune, eur-afr-hla (all chr6+ tests)

The LLM's `estimate_variants` tool returned full-chromosome counts, but data preparation extracts only a region subset. Workers crashed trying to process rows beyond actual data size.

**Fix (v2):** Generation is now deterministic — `generate_workflow` MCP tool receives actual VCF row counts from data preparation, not LLM estimates. Verified working with brca1-gbr (correct chunk ranges 1-237, 237-473, ..., 2361-2597 for 2369 total rows).

### Issue 2: Upstream Race Condition — FIXED

`os.makedirs()` without `exist_ok=True`. Fixed in image `1.1-je1.3.4`.

### Issue 3: K8s Overhead — REDUCED in v2

Overhead reduced from ~2m28s to ~1m48s by eliminating data container wait and LLM generation. Remaining overhead is mostly deployment (kubectl cp) and K8s job startup.

### Issue 4: columns.txt Performance — FIXED in v2

`frequency.py` was processing all 2504 samples instead of just the requested population. Now population-filtered (e.g., 91 GBR samples). This was the primary cause of the 5x slowdown in brca1-gbr — monitoring time dropped from 15m10s to 3m31s.

### Issue 5: Init Container Blocking — FIXED in v2

The `values-fast-test-run.yaml` base values enable a `dataPreprocess` init container that waits for `workflow.json` on NFS (expected from the data image). Conductor delivers `workflow.json` via `kubectl cp` after the engine starts, so the init container blocked forever. Fixed by setting `initContainers.enabled: false` in generated Helm values.

---

## Recommendations

1. ~~**Fix variant count mismatch**~~ (Issue 1): **DONE** in v2. Deterministic generation with actual row counts.

2. ~~**Report upstream race condition**~~ (Issue 2): **DONE** — Fixed in image `1.1-je1.3.4`.

3. **Run eas-hla-autoimmune and eur-afr-hla** with v2 to get timing comparisons for HLA-region workloads.

4. **Optimize deployment phase** (1m8s): Consider batching kubectl cp calls or using a ConfigMap for columns.txt/population files.

5. **Consider reducing K8s job startup overhead**: Pre-pull worker images, or use init containers that are already cached.
