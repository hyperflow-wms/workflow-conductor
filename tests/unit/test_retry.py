"""Unit tests for retry logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.k8s.cluster import ClusterError
from workflow_conductor.k8s.helm import HelmError
from workflow_conductor.k8s.kubectl import KubectlError
from workflow_conductor.models import PipelineState
from workflow_conductor.retry import is_transient_error, run_with_retry


class TestIsTransientError:
    def test_kubectl_error_is_transient(self) -> None:
        assert is_transient_error(KubectlError("connection refused")) is True

    def test_helm_error_is_transient(self) -> None:
        assert is_transient_error(HelmError("timed out")) is True

    def test_cluster_error_is_transient(self) -> None:
        assert is_transient_error(ClusterError("cluster not ready")) is True

    def test_timeout_error_is_transient(self) -> None:
        assert is_transient_error(TimeoutError("deadline exceeded")) is True

    def test_connection_error_is_transient(self) -> None:
        assert is_transient_error(ConnectionError("refused")) is True

    def test_os_error_is_transient(self) -> None:
        assert is_transient_error(OSError("network unreachable")) is True

    def test_value_error_not_transient(self) -> None:
        assert is_transient_error(ValueError("bad input")) is False

    def test_key_error_not_transient(self) -> None:
        assert is_transient_error(KeyError("missing")) is False

    def test_generic_exception_not_transient(self) -> None:
        assert is_transient_error(Exception("unknown")) is False


class TestRunWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self) -> None:
        state = PipelineState()
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        with patch("workflow_conductor.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await run_with_retry(
                succeed, max_retries=3, backoff_base=2.0, phase="test", state=state
            )
        assert result == "ok"
        assert call_count == 1
        assert len(state.errors) == 0

    @pytest.mark.asyncio
    async def test_retry_then_succeed(self) -> None:
        state = PipelineState()
        call_count = 0

        async def fail_twice_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise KubectlError(f"transient failure #{call_count}")
            return "recovered"

        with patch("workflow_conductor.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await run_with_retry(
                fail_twice_then_succeed,
                max_retries=3,
                backoff_base=2.0,
                phase="deployment",
                state=state,
            )
        assert result == "recovered"
        assert call_count == 3
        assert len(state.errors) == 2
        assert state.errors[0].phase == "deployment"
        assert state.errors[0].recoverable is True

    @pytest.mark.asyncio
    async def test_exhaust_retries_raises(self) -> None:
        state = PipelineState()

        async def always_fail() -> str:
            raise HelmError("persistent failure")

        with (
            patch("workflow_conductor.retry.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HelmError, match="persistent failure"),
        ):
            await run_with_retry(
                always_fail,
                max_retries=2,
                backoff_base=2.0,
                phase="deployment",
                state=state,
            )
        # 2 retryable errors + 1 final non-recoverable = 3 total
        assert len(state.errors) == 3
        assert state.errors[-1].recoverable is False
        assert "exhausted" in state.errors[-1].suggested_action

    @pytest.mark.asyncio
    async def test_non_transient_raises_immediately(self) -> None:
        state = PipelineState()
        call_count = 0

        async def raise_value_error() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("bad config")

        with pytest.raises(ValueError, match="bad config"):
            await run_with_retry(
                raise_value_error,
                max_retries=3,
                backoff_base=2.0,
                phase="planning",
                state=state,
            )
        assert call_count == 1
        assert len(state.errors) == 0

    @pytest.mark.asyncio
    async def test_exponential_backoff(self) -> None:
        state = PipelineState()
        call_count = 0

        async def fail_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise KubectlError("retry me")
            return "ok"

        with patch(
            "workflow_conductor.retry.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await run_with_retry(
                fail_then_succeed,
                max_retries=5,
                backoff_base=2.0,
                phase="test",
                state=state,
            )
        # Backoff: 2^1=2, 2^2=4, 2^3=8
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)
        mock_sleep.assert_any_call(8.0)

    @pytest.mark.asyncio
    async def test_zero_retries_raises_on_first_failure(self) -> None:
        state = PipelineState()

        async def fail() -> str:
            raise KubectlError("fail")

        with pytest.raises(KubectlError):
            await run_with_retry(
                fail,
                max_retries=0,
                backoff_base=2.0,
                phase="test",
                state=state,
            )
        # With 0 retries, the single failure is recorded as non-recoverable
        assert len(state.errors) == 1
        assert state.errors[0].recoverable is False
