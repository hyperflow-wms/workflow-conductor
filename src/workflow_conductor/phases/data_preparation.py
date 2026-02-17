"""Phase 5: Data Preparation — decompress and scan data on NFS volume.

After provisioning (hf-ops + hf-run + nfs-data), this phase:
- Validates requested chromosomes are available in the data container
- Decompresses VCF and annotation files from /work_dir/20130502/ to /work_dir/
- Copies population files and columns.txt to /work_dir/
- Scans VCF files for exact row counts
- Populates state.chromosome_data for workflow generation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Kubectl
from workflow_conductor.models import (
    ChromosomeData,
    PipelinePhase,
    PipelineState,
)
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_data_preparation_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Decompress data files, scan row counts, populate chromosome_data.

    Requires state.engine_pod_name and state.namespace from provisioning.
    The nfs-data job has already staged compressed files to /work_dir/20130502/.
    """
    display_phase_header(PipelinePhase.DATA_PREPARATION)

    if not state.engine_pod_name:
        raise ValueError(
            "Cannot prepare data: engine pod not set (provisioning phase required)"
        )
    if not state.namespace:
        raise ValueError(
            "Cannot prepare data: namespace not set (provisioning phase required)"
        )

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    namespace = state.namespace

    # Step 1: Determine needed chromosomes from workflow plan
    chromosomes = state.workflow_plan.chromosomes if state.workflow_plan else []
    if not chromosomes:
        raise ValueError("No chromosomes specified in workflow plan")

    # Step 2: Check availability (data container ships chr1-10 only)
    missing = [c for c in chromosomes if c not in settings.data_container_chromosomes]
    if missing:
        raise ValueError(
            f"Chromosomes {missing} not available in data container "
            f"(only chr{','.join(settings.data_container_chromosomes)} included). "
            "Remote tabix extraction not yet supported."
        )

    logger.info("Step 1: Preparing data for chromosomes: %s", ", ".join(chromosomes))

    # Step 3: Decompress + flatten data files (exec in engine pod)
    # The data container stages files to /work_dir/20130502/ as compressed .vcf.gz
    # The workflow expects decompressed .vcf files directly in /work_dir/
    decompress_cmd = (
        "cd /work_dir/20130502 && "
        "for f in ALL.chr*.vcf.gz; do "
        '  gunzip -c "$f" > "/work_dir/$(basename $f .gz)" ; '
        "done && "
        "cp columns.txt /work_dir/ 2>/dev/null || true && "
        "cp /work_dir/populations/* /work_dir/ 2>/dev/null || true"
    )
    logger.info("Step 2: Decompressing and staging data files")
    await kubectl.exec_in_pod(
        state.engine_pod_name,
        ["/bin/sh", "-c", decompress_cmd],
        namespace=namespace,
        container="hyperflow",
        timeout=600,
    )

    # Step 4: Scan VCF files for exact row counts
    logger.info("Step 3: Scanning VCF files for row counts")
    scan_cmd = " && ".join(
        f'echo "{c}:$(grep -c -v "^#" /work_dir/ALL.chr{c}.250000.vcf)"'
        for c in chromosomes
    )
    output = await kubectl.exec_in_pod(
        state.engine_pod_name,
        ["/bin/sh", "-c", scan_cmd],
        namespace=namespace,
        container="hyperflow",
        timeout=300,
    )

    # Step 5: Parse output → chromosome_data
    state.chromosome_data = []
    for line in output.strip().splitlines():
        parts = line.split(":")
        if len(parts) != 2:
            continue
        chrom, count_str = parts
        chrom = chrom.strip()
        try:
            row_count = int(count_str.strip())
        except ValueError:
            logger.warning("Could not parse row count for chr%s: %s", chrom, count_str)
            continue

        state.chromosome_data.append(
            ChromosomeData(
                chromosome=chrom,
                vcf_file=f"ALL.chr{chrom}.250000.vcf",
                row_count=row_count,
                annotation_file=(
                    f"ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5"
                    f".20130502.sites.annotation.vcf"
                ),
            )
        )
        logger.info("  chr%s: %d variants", chrom, row_count)

    if not state.chromosome_data:
        raise ValueError("No chromosome data could be scanned from VCF files")

    logger.info(
        "Data preparation complete: %d chromosomes ready",
        len(state.chromosome_data),
    )
    return state
