# 1000 Genomes Workflow — Reference for Workflow Conductor

## What It Is

A 5-stage genomics analysis pipeline processing VCF (Variant Call Format) files from the [1000 Genomes Project](https://www.internationalgenome.org/) to identify mutational overlaps and allele frequencies across human populations. Originally a [Pegasus workflow](https://github.com/pegasus-isi/1000genome-workflow) (USC), ported to HyperFlow.

## Pipeline Structure

```
individuals (×N parallel) → individuals_merge → sifting ─┐
                                                          ├→ mutation_overlap (×P populations)
                                                          └→ frequency (×P populations)
```

**Task count formula:** `C × (J + 2 + 2P)` where:
- C = number of chromosomes (1–22)
- J = parallel jobs per chromosome (individuals stage)
- P = number of populations analyzed

Example: 5 chromosomes, 10 jobs/chr, 2 populations = 5 × (10 + 2 + 4) = 80 tasks.

## Stage Details

| Stage | Script | Input | Output | Parallel? |
|-------|--------|-------|--------|-----------|
| **individuals** | `individuals.py` | VCF file + row range | `.tar.gz` per-sample variant data | Yes — N times per chromosome |
| **individuals_merge** | `individuals_merge.py` | N `.tar.gz` archives | Single merged `.tar.gz` | No — 1 per chromosome |
| **sifting** | `sifting.py` | Annotation VCF (SIFT scores) | `sifted.SIFT.txt` | No — 1 per chromosome |
| **mutation_overlap** | `mutation_overlap.py` | Merged data + sifted data + population file | `.tar.gz` overlap counts + PNG plots | Per population |
| **frequency** | `frequency.py` | Merged data + population file | `.tar.gz` frequency data + PNG plots | Per population |

### What Each Stage Does

**individuals** — Partitions a chromosome's VCF file by row range. Each parallel task reads a slice of variant rows and extracts per-sample genotype data. Handles both gzip and plain text VCF formats.

**individuals_merge** — Concatenates the per-sample files from all parallel partitions into a single archive per chromosome.

**sifting** — Processes SIFT annotation data to classify variants as "deleterious" or "tolerated". Produces a filtered annotation file used by mutation_overlap.

**mutation_overlap** — For each population, reads the merged individual data and sifted annotations to count shared mutations. Produces statistical results and matplotlib plots.

**frequency** — For each population, calculates allele frequency distributions from the merged individual data. Produces frequency statistics and matplotlib plots.

## Data Access: tabix for Remote VCF Extraction

[tabix](http://www.htslib.org/doc/tabix.html) is a tool for indexing and querying sorted, compressed genomic data files. It enables **random access** to specific genomic regions without downloading entire chromosome VCF files (which can be 1–10 GB each).

### Why tabix Matters for the Conductor

The Workflow Composer's PLAN phase generates `tabix` commands when the user requests analysis of specific genomic regions (e.g., "BRCA1 region on chromosome 17"). These commands are part of the data preparation step that runs *before* the main workflow.

```bash
# Extract a specific region from a remote VCF (only downloads ~MBs instead of ~GBs)
tabix -h https://ftp.1000genomes.ebi.ac.uk/.../ALL.chr6.vcf.gz 6:28477797-33448354 | bgzip > region.vcf.gz

# Index the extracted file for downstream processing
tabix -p vcf region.vcf.gz
```

### Data Sources

| Source | URL | Best for |
|--------|-----|----------|
| AWS S3 | `s3://1000genomes/release/20130502/` | AWS deployments |
| Google Cloud | `gs://genomics-public-data/.../1000genomes/ftp/release/20130502/` | GCP deployments |
| FTP | `ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/` | Universal fallback |
| Data container | `hyperflowwms/1000genome-data:1.0` (~1.7 GB) | Local/Kind testing |

The pre-built data container includes VCF files (~250K rows/chr for chr1-10), SIFT annotations (~1.2 GB), population files, and a pre-generated workflow.json. This is what the Kind test cluster uses.

## Populations

### Super-populations (Continental)

| Code | Name | Samples |
|------|------|---------|
| **AFR** | African | 661 |
| **AMR** | Americas (Mixed) | 347 |
| **EAS** | East Asian | 504 |
| **EUR** | European | 503 |
| **SAS** | South Asian | 489 |

### Sub-populations (Selected)

- **AFR**: YRI, LWK, GWD, MSL, ESN, ASW, ACB
- **EUR**: CEU, TSI, FIN, GBR, IBS
- **EAS**: CHB, JPT, CHS, CDX, KHV
- **SAS**: GIH, PJL, BEB, STU, ITU
- **AMR**: MXL, PUR, CLM, PEL

## Well-Known Genomic Regions (GRCh37/hg19)

| Region | Chr | Coordinates | Size | Disease Association |
|--------|-----|-------------|------|---------------------|
| **HLA** | 6 | 28477797–33448354 | ~5 Mb | Immune function, autoimmune |
| **BRCA1** | 17 | 43044295–43125483 | ~81 kb | Breast/ovarian cancer |
| **BRCA2** | 13 | 32315086–32400266 | ~85 kb | Breast/ovarian cancer |
| **APOE** | 19 | 44905796–44909393 | ~3.6 kb | Alzheimer's disease |
| **CYP2D6** | 22 | 42518900–42528000 | ~9 kb | Drug metabolism |
| **HBB** | 11 | 5225464–5229395 | ~4 kb | Sickle cell disease |
| **CFTR** | 7 | 117120017–117308718 | ~189 kb | Cystic fibrosis |
| **TP53** | 17 | 7668421–7687490 | ~19 kb | Cancer |

## Parallelism Presets

| Preset | Jobs/chromosome | Use case |
|--------|-----------------|----------|
| small | 10 | Testing, small regions |
| medium/moderate | 50 | Standard analysis |
| large | 250 | Full genome |

**Adaptive parallelism:** When vCPUs are specified, targets ~25,000 variants per task with bounds of 1.5×–5× the vCPU count.

## Docker Images

| Image | Tag | Description |
|-------|-----|-------------|
| `hyperflowwms/1000genome-worker` | `1.0-je1.3.4` | HyperFlow worker with job-executor |
| `hyperflowwms/1000genome-data` | `1.0` | Input data (~1.7 GB) |
| `hyperflowwms/1000genome-mcp` | `2.0` | MCP server (Workflow Composer) |
| `hyperflowwms/hyperflow` | `latest` | HyperFlow engine |

## MCP Tools (Workflow Composer)

The Conductor interacts with the Workflow Composer via these MCP tools:

| Tool | Purpose |
|------|---------|
| `plan_workflow` | Parse NL prompt → advisory plan with data prep steps |
| `generate_workflow` | Plan + measurements → final workflow.json DAG |
| `estimate_variants` | Estimate variant counts for regions/chromosomes |
| `list_known_regions` | List well-known genomic regions (HLA, BRCA, etc.) |
| `list_populations` | List available populations and sample counts |

## Deferred Workflow Generation (Key Architectural Pattern)

The 1000 Genomes workflow has a fundamental characteristic: **the optimal DAG structure cannot be determined until data is physically present on the execution environment**.

### The Problem

When the Workflow Composer creates an initial plan (Phase 2), it knows *which* files each task needs (VCF files, annotations, population lists) but **not the precise sizes** of the actual data. This matters because:

1. **Parallelism depends on data size.** The `individuals` stage splits a chromosome's VCF file into N parallel tasks. Too few tasks = each task gets too much data and runs out of memory. Too many tasks = scheduling overhead exceeds computation time.
2. **Estimated sizes may differ from reality.** FTP mirrors, compression ratios, and file format versions can cause actual file sizes to differ from lookup-table estimates.
3. **Available compute is unknown at planning time.** The number of vCPUs on the cluster affects optimal parallelism (target: ~25,000 variants per task, bounded by 1.5×–5× vCPU count).

### The Solution: Plan → Provision → Measure → Regenerate

```
Phase 2: PLAN        → Composer creates advisory plan (parallelism = TBD)
Phase 5: PROVISION   → Data downloaded to shared NFS volume on the K8s cluster
                     → Measure actual VCF file sizes + count available vCPUs
Phase 6: GENERATE    → Composer regenerates final workflow.json with real measurements
                     → Optimal parallelism now determined from actual data + cluster capacity
```

1. **Plan phase** — The Composer produces a workflow plan with estimated data sizes from built-in lookup tables. Parallelism is left unresolved (`null`).
2. **Provision phase** — Data is downloaded to the shared PersistentVolume (NFS) on the K8s cluster where the workflow will execute. After download, actual file sizes are measured and available vCPUs are counted.
3. **Deferred generation** — The Composer receives the real measurements and generates the final `workflow.json` DAG with optimal parallelism. This is when task counts, memory allocations, and the full DAG structure are locked in.

### Why This Matters for the Conductor

- **Stage 1 (MVP)** skips this pattern — uses the Composer's estimated variant counts directly to generate the DAG in a single pass.
- **Stage 2** implements the full pattern: separate provisioning + measurement phases, then deferred generation with context replay from the earlier planning conversation.
- The Conductor must **serialize the planning conversation history** (`planner_history`) so it can replay context into the deferred generation call — the Composer is stateless and needs the full context on each invocation.

### Example Measurements

```json
{
  "actual_data_sizes": {
    "chr1.vcf.gz": 1247,
    "chr2.vcf.gz": 1312,
    "chr5.vcf.gz": 998
  },
  "total_data_size_mb": 5748,
  "available_vcpus": 32
}
```

With 32 vCPUs and 5 chromosomes, the Composer might choose parallelism=10, producing 5 × (10 + 2 + 4) = 80 tasks with ~3.17 GB peak memory per worker.

## References

- [1000 Genomes Project](https://www.internationalgenome.org/)
- [Pegasus 1000genome-workflow](https://github.com/pegasus-isi/1000genome-workflow)
- [HyperFlow WMS](https://hyperflow.2.vu.nl/)
- [tabix documentation](http://www.htslib.org/doc/tabix.html)
- [VCF specification](https://samtools.github.io/hts-specs/VCFv4.3.pdf)
