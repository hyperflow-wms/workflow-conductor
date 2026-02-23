"""Phase 5: Data Preparation — fetch data via tabix/curl and scan VCFs.

After provisioning (hf-ops + hf-run), this phase:
- Downloads all VCF/annotation files via tabix or curl commands from the
  Composer-generated plan (no data container dependency)
- Scans all VCF files for exact row counts
- Extracts VCF header for columns.txt generation in the generation phase
- Populates state.chromosome_data for workflow generation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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


def _filter_commands_for_chromosomes(
    commands: list[str], chromosomes: list[str]
) -> list[str]:
    """Filter download commands to only include specified chromosomes."""
    filtered = []
    for cmd in commands:
        for chrom in chromosomes:
            if f"chr{chrom}." in cmd or f"chr{chrom} " in cmd:
                filtered.append(cmd)
                break
    return filtered


def _build_tabix_job(commands: list[str], namespace: str, image: str) -> dict[str, Any]:
    """Build K8s Job manifest for remote VCF extraction."""
    script = " && ".join(commands)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": "tabix-extract",
            "namespace": namespace,
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {"app": "hyperflow", "role": "tabix-extract"},
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "tabix",
                            "image": image,
                            "workingDir": "/work_dir",
                            "command": ["/bin/sh", "-c", script],
                            "volumeMounts": [
                                {
                                    "name": "workflow-data",
                                    "mountPath": "/work_dir",
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "512Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "workflow-data",
                            "persistentVolumeClaim": {"claimName": "nfs"},
                        }
                    ],
                },
            },
        },
    }


async def run_data_preparation_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Download VCF data, scan row counts, extract VCF header.

    Requires state.engine_pod_name and state.namespace from provisioning.

    All data is fetched via tabix/curl commands from the Composer plan.
    No data container is used.
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

    # Step 2: Download data files via K8s Job
    download_commands = (
        state.workflow_plan.download_commands if state.workflow_plan else []
    )
    if not download_commands:
        raise ValueError(
            "No download commands available from workflow planner. "
            "The Composer plan must include data_preparation commands."
        )

    commands = _filter_commands_for_chromosomes(download_commands, chromosomes)
    if not commands:
        raise ValueError(f"No download commands found for chromosomes {chromosomes}")

    logger.info(
        "Step 1: Downloading data for chr%s (%d commands)",
        ",".join(chromosomes),
        len(commands),
    )
    job_manifest = _build_tabix_job(
        commands=commands,
        namespace=namespace,
        image=settings.tabix_image,
    )
    await kubectl.apply_json(job_manifest, namespace=namespace)
    await kubectl.wait_for_job(
        "tabix-extract",
        namespace=namespace,
        timeout=settings.tabix_job_timeout,
    )

    # Step 3: Discover VCF files and scan for exact row counts.
    # File naming varies by Composer version (e.g. ALL.chr17.250000.vcf vs
    # ALL.chr17.brca1.vcf), so we discover them dynamically.
    logger.info("Step 2: Scanning VCF files for row counts")
    discover_cmd = (
        "for c in " + " ".join(chromosomes) + "; do "
        "  vcf=$(ls /work_dir/ALL.chr${c}.*.vcf 2>/dev/null "
        "    | grep -v annotation | grep -v sites | head -1); "
        "  ann=$(ls /work_dir/ALL.chr${c}.*annotation*.vcf 2>/dev/null | head -1); "
        '  if [ -n "$vcf" ]; then '
        '    count=$(grep -c -v "^#" "$vcf"); '
        '    echo "${c}:${count}:$(basename $vcf):$(basename ${ann:-none})"; '
        "  fi; "
        "done"
    )
    output = await kubectl.exec_in_pod(
        state.engine_pod_name,
        ["/bin/sh", "-c", discover_cmd],
        namespace=namespace,
        container="hyperflow",
        timeout=300,
    )

    # Step 4: Parse output → chromosome_data
    # Format: "17:1234:ALL.chr17.brca1.vcf:ALL.chr17.brca1.annotation.vcf"
    state.chromosome_data = []
    for line in output.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        chrom = parts[0].strip()
        count_str = parts[1].strip()
        vcf_file = parts[2].strip()
        ann_file = parts[3].strip() if len(parts) > 3 and parts[3] != "none" else ""
        try:
            row_count = int(count_str)
        except ValueError:
            logger.warning("Could not parse row count for chr%s: %s", chrom, count_str)
            continue

        state.chromosome_data.append(
            ChromosomeData(
                chromosome=chrom,
                vcf_file=vcf_file,
                row_count=row_count,
                annotation_file=ann_file,
            )
        )
        logger.info("  chr%s: %d variants (%s)", chrom, row_count, vcf_file)

    if not state.chromosome_data:
        raise ValueError("No chromosome data could be scanned from VCF files")

    # Step 5: Extract VCF header from the first VCF for columns.txt generation.
    # The #CHROM line contains all sample IDs, which the generation phase uses
    # to create a population-filtered columns.txt.
    first_vcf = state.chromosome_data[0].vcf_file
    logger.info("Step 3: Extracting VCF header from %s", first_vcf)
    header_cmd = f"head -1000 /work_dir/{first_vcf} | grep '^#CHROM'"
    vcf_header = await kubectl.exec_in_pod(
        state.engine_pod_name,
        ["/bin/sh", "-c", header_cmd],
        namespace=namespace,
        container="hyperflow",
        timeout=30,
    )
    state.vcf_header = vcf_header.strip()
    header_fields = state.vcf_header.split("\t")
    logger.info(
        "VCF header: %d fields (%d samples)",
        len(header_fields),
        max(0, len(header_fields) - 9),
    )

    logger.info(
        "Data preparation complete: %d chromosomes ready",
        len(state.chromosome_data),
    )
    return state
