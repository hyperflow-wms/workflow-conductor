"""E2E output verification tests — run full pipeline and verify output files.

Each test case runs the complete 10-phase pipeline, then verifies that
specific output files exist and are non-empty in the engine pod's /work_dir/.
A detailed markdown report is generated for every run (pass or fail).

Run with: make test-e2e-outputs
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.e2e.cases import E2E_CASES, E2ETestCase
from tests.e2e.report import generate_report
from workflow_conductor.app import run_pipeline
from workflow_conductor.config import ConductorSettings
from workflow_conductor.k8s import Helm, Kubectl
from workflow_conductor.models import PipelineState, PipelineStatus

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def no_teardown_settings(e2e_settings: ConductorSettings) -> ConductorSettings:
    """E2E settings with no_teardown=True and extended timeout."""
    return e2e_settings.model_copy(
        update={
            "no_teardown": True,
            "monitor_timeout": 3000,
        },
    )


async def _collect_file_sizes(
    kubectl: Kubectl,
    pod: str,
    namespace: str,
    filenames: list[str],
) -> dict[str, int | None]:
    """Check each expected file and return filename -> size (None if missing)."""
    result: dict[str, int | None] = {}
    for filename in filenames:
        try:
            size_str = await kubectl.exec_in_pod(
                pod,
                ["stat", "-c", "%s", f"/work_dir/{filename}"],
                namespace=namespace,
                container="hyperflow",
            )
            result[filename] = int(size_str.strip())
        except Exception:
            result[filename] = None
    return result


async def _teardown_namespace(
    kubectl: Kubectl,
    helm: Helm,
    namespace: str,
    namespace_prefix: str,
) -> None:
    """Clean up namespace after test (mirrors completion.py teardown)."""
    logger.info("Tearing down namespace: %s", namespace)
    for release in ["hf-run", "hf-data", "hf-ops"]:
        try:
            if await helm.release_exists(release, namespace=namespace):
                await helm.uninstall(release, namespace=namespace)
        except Exception:
            logger.warning("Failed to uninstall %s", release, exc_info=True)

    try:
        await kubectl.delete_namespace(namespace)
    except Exception:
        logger.warning("Failed to delete namespace %s", namespace, exc_info=True)

    try:
        await kubectl.cleanup_previous_runs(
            namespace_prefix,
            current_namespace="",
        )
    except Exception:
        logger.warning("Failed to clean up cluster resources", exc_info=True)


@pytest.mark.e2e
@pytest.mark.timeout(4200)
class TestOutputVerification:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case",
        E2E_CASES,
        ids=[c.case_id for c in E2E_CASES],
    )
    async def test_output_files(
        self,
        case: E2ETestCase,
        no_teardown_settings: ConductorSettings,
    ) -> None:
        """Run pipeline and verify expected output files exist."""
        state: PipelineState | None = None
        file_sizes: dict[str, int | None] = {}
        jobs_data: dict | None = None
        columns_txt = ""
        engine_logs = ""
        error_message = ""
        passed = False

        kubeconfig = no_teardown_settings.kubernetes.kubeconfig
        kubectl = Kubectl(kubeconfig=kubeconfig)
        helm = Helm(kubeconfig=kubeconfig)

        try:
            # Run the full pipeline
            state = await asyncio.wait_for(
                run_pipeline(
                    case.prompt,
                    no_teardown_settings,
                    auto_approve=True,
                ),
                timeout=case.timeout_seconds,
            )

            # --- Pipeline-level assertions ---
            assert state.status == PipelineStatus.COMPLETED, (
                f"Pipeline status: {state.status}"
            )
            assert state.workflow_status == "completed", (
                f"Workflow status: {state.workflow_status}"
            )
            assert len(state.phase_results) == 10, (
                f"Expected 10 phases, got {len(state.phase_results)}"
            )

            # --- Plan assertions (LLM sanity guard) ---
            assert state.workflow_plan is not None
            plan_chroms = sorted(state.workflow_plan.chromosomes)
            expected_chroms = sorted(case.expected_chromosomes)
            assert plan_chroms == expected_chroms, (
                f"Chromosomes: expected {expected_chroms}, got {plan_chroms}"
            )

            plan_pops = sorted(state.workflow_plan.populations)
            expected_pops = sorted(case.expected_populations)
            assert plan_pops == expected_pops, (
                f"Populations: expected {expected_pops}, got {plan_pops}"
            )

            # --- Execution assertions ---
            assert state.execution_summary is not None
            assert state.execution_summary.failed_tasks == 0, (
                f"Failed tasks: {state.execution_summary.failed_tasks}"
            )

            # --- Output file verification ---
            assert state.engine_pod_name, "Engine pod name not set"
            assert state.namespace, "Namespace not set"

            file_sizes = await _collect_file_sizes(
                kubectl, state.engine_pod_name, state.namespace, case.expected_files
            )

            missing = [f for f, s in file_sizes.items() if s is None]
            empty = [f for f, s in file_sizes.items() if s == 0]
            assert not missing, f"Missing output files: {missing}"
            assert not empty, f"Empty output files: {empty}"

            passed = True

        except Exception as exc:
            error_message = str(exc)
            raise

        finally:
            # Collect extra data for the report (best-effort)
            if state and state.namespace and state.engine_pod_name:
                try:
                    jobs_data = await kubectl.get_json(
                        "jobs",
                        namespace=state.namespace,
                        label_selector="app=hyperflow",
                    )
                except Exception:
                    logger.warning("Failed to collect job data for report")

                try:
                    columns_txt = await kubectl.exec_in_pod(
                        state.engine_pod_name,
                        ["cat", "/work_dir/columns.txt"],
                        namespace=state.namespace,
                        container="hyperflow",
                    )
                except Exception:
                    logger.warning("Failed to collect columns.txt for report")

                try:
                    engine_logs = await kubectl.logs(
                        state.engine_pod_name,
                        namespace=state.namespace,
                        container="hyperflow",
                        tail=200,
                    )
                except Exception:
                    logger.warning("Failed to collect engine logs for report")

            # Generate report (even on failure)
            if state:
                try:
                    report_path = generate_report(
                        case_id=case.case_id,
                        state=state,
                        passed=passed,
                        file_sizes=file_sizes,
                        jobs_data=jobs_data,
                        columns_txt=columns_txt,
                        engine_logs=engine_logs,
                        error_message=error_message,
                    )
                    logger.info("Report: %s", report_path)
                except Exception:
                    logger.warning("Failed to generate report", exc_info=True)

            # Teardown namespace
            if state and state.namespace:
                await _teardown_namespace(
                    kubectl,
                    helm,
                    state.namespace,
                    no_teardown_settings.kubernetes.namespace_prefix,
                )
