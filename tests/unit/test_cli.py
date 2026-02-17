"""Unit tests for CLI argument parsing and commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from workflow_conductor.cli import main


class TestMainGroup:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Workflow Conductor" in result.output

    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output


class TestRunCommand:
    """Test CLI argument parsing — run_pipeline is mocked to avoid
    Docker/MCP/LLM calls that make tests take 25+ seconds."""

    def _invoke(self, args: list[str]) -> tuple:
        """Invoke CLI with run_pipeline mocked; return (result, mock)."""
        mock_pipeline = AsyncMock()
        with patch("workflow_conductor.cli.run_pipeline", mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(main, args)
        return result, mock_pipeline

    def test_default_prompt(self) -> None:
        result, mock = self._invoke(["run"])
        assert result.exit_code == 0
        mock.assert_called_once()
        call_args = mock.call_args
        assert "chromosome 22" in call_args[0][0]  # default prompt

    def test_custom_prompt(self) -> None:
        result, mock = self._invoke(["run", "Analyze GBR, chr 1"])
        assert result.exit_code == 0
        assert mock.call_args[0][0] == "Analyze GBR, chr 1"

    def test_dry_run_flag(self) -> None:
        result, mock = self._invoke(["run", "--dry-run"])
        assert result.exit_code == 0
        assert mock.call_args[1]["dry_run"] is True

    def test_auto_approve_flag(self) -> None:
        result, mock = self._invoke(["run", "--auto-approve"])
        assert result.exit_code == 0
        assert mock.call_args[1]["auto_approve"] is True

    def test_demo_flag(self) -> None:
        result, mock = self._invoke(["run", "--demo"])
        assert result.exit_code == 0
        assert mock.call_args[1]["demo"] is True

    def test_no_pause_flag(self) -> None:
        result, mock = self._invoke(["run", "--demo", "--no-pause"])
        assert result.exit_code == 0
        assert mock.call_args[1]["demo"] is True
        assert mock.call_args[1]["no_pause"] is True

    def test_log_level_option(self) -> None:
        result, _ = self._invoke(["--log-level", "DEBUG", "run"])
        assert result.exit_code == 0
