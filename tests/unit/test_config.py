"""Unit tests for config.py."""

from __future__ import annotations

import os
from unittest.mock import patch

from workflow_conductor.config import ConductorSettings


def _defaults() -> ConductorSettings:
    """Construct settings without loading .env file (test pure code defaults)."""
    return ConductorSettings(_env_file=None)  # type: ignore[call-arg]


class TestConductorSettingsDefaults:
    def test_default_cluster_name(self) -> None:
        assert _defaults().kubernetes.cluster_name == "hyperflow-test"

    def test_default_provider(self) -> None:
        assert _defaults().llm.default_provider == "anthropic"

    def test_default_behavior_flags(self) -> None:
        settings = _defaults()
        assert settings.auto_approve is False
        assert settings.auto_teardown is False
        assert settings.skip_profiler is True
        assert settings.verbose is False

    def test_default_images(self) -> None:
        settings = _defaults()
        assert "hyperflowwms/hyperflow" in settings.hf_engine_image
        assert "1000genome-worker" in settings.worker_image
        assert "1000genome-data" in settings.data_image

    def test_default_helm_timeouts(self) -> None:
        settings = _defaults()
        assert settings.helm.timeout_ops == "15m"
        assert settings.helm.timeout_run == "10m"

    def test_default_monitor_settings(self) -> None:
        settings = _defaults()
        assert settings.monitor_poll_interval == 10
        assert settings.monitor_timeout == 3600

    def test_default_composer_server_command(self) -> None:
        assert _defaults().workflow.composer_server_command == "docker"

    def test_default_composer_server_args_contain_image(self) -> None:
        assert (
            "hyperflowwms/1000genome-mcp:2.1"
            in _defaults().workflow.composer_server_args
        )


class TestConductorSettingsEnvOverride:
    def test_env_auto_approve(self) -> None:
        with patch.dict(os.environ, {"HF_CONDUCTOR_AUTO_APPROVE": "true"}):
            settings = ConductorSettings()
            assert settings.auto_approve is True

    def test_env_log_level(self) -> None:
        with patch.dict(os.environ, {"HF_CONDUCTOR_LOG_LEVEL": "DEBUG"}):
            settings = ConductorSettings()
            assert settings.log_level == "DEBUG"

    def test_env_nested_kubernetes(self) -> None:
        with patch.dict(
            os.environ,
            {"HF_CONDUCTOR_KUBERNETES__CLUSTER_NAME": "my-cluster"},
        ):
            settings = ConductorSettings()
            assert settings.kubernetes.cluster_name == "my-cluster"

    def test_env_nested_llm_provider(self) -> None:
        with patch.dict(
            os.environ,
            {"HF_CONDUCTOR_LLM__DEFAULT_PROVIDER": "google"},
        ):
            settings = ConductorSettings()
            assert settings.llm.default_provider == "google"

    def test_env_nested_helm_charts_path(self) -> None:
        with patch.dict(
            os.environ,
            {"HF_CONDUCTOR_HELM__CHARTS_PATH": "/custom/charts"},
        ):
            settings = ConductorSettings()
            assert settings.helm.charts_path == "/custom/charts"


class TestConductorSettingsConstructor:
    def test_override_via_constructor(self) -> None:
        settings = ConductorSettings(
            auto_approve=True,
            log_level="DEBUG",
            resource_quota_cpu="10",
        )
        assert settings.auto_approve is True
        assert settings.log_level == "DEBUG"
        assert settings.resource_quota_cpu == "10"

    def test_nested_via_dict(self) -> None:
        settings = ConductorSettings(
            kubernetes={"cluster_name": "test-cluster"},  # type: ignore[arg-type]
        )
        assert settings.kubernetes.cluster_name == "test-cluster"
