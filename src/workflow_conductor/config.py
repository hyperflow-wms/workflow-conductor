"""Nested pydantic-settings with HF_CONDUCTOR_ prefix and __ delimiter."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KubernetesSettings(BaseSettings):
    cluster_name: str = "hyperflow-test"
    cluster_provider: str = "kind"  # "kind" | "k3d" | "existing"
    kind_config: str = "local/kind-config-1n.yaml"
    kubeconfig: str = ""
    namespace_prefix: str = "wf-1000g"


class HelmSettings(BaseSettings):
    charts_path: str = ""
    ops_values: str = ""
    run_values: str = ""
    timeout_ops: str = "15m"
    timeout_run: str = "10m"


class WorkflowSettings(BaseSettings):
    composer_server_command: str = "docker"
    composer_server_args: list[str] = Field(
        default_factory=lambda: [
            "run",
            "--rm",
            "-i",
            "hyperflowwms/1000genome-mcp:2.3",
            "python",
            "-m",
            "workflow_composer.mcp_server",
        ]
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
        env_file_encoding="utf-8",
        extra="ignore",
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
    demo: bool = False
    log_level: str = "INFO"
    max_workflow_processes: int = 200
    data_container_chromosomes: list[str] = Field(
        default_factory=lambda: [str(i) for i in range(1, 11)]
    )
    tabix_image: str = "broadinstitute/gatk:4.4.0.0"
    tabix_job_timeout: int = 600
    monitor_poll_interval: int = 10
    monitor_timeout: int = 3600
