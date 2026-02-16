"""Unit tests for CLI argument parsing and commands."""

from __future__ import annotations

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
    def test_default_prompt(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run"])
        assert result.exit_code == 0
        assert "Workflow Conductor" in result.output

    def test_custom_prompt(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run", "Analyze GBR, chr 1"])
        assert result.exit_code == 0
        assert "Analyze GBR, chr 1" in result.output

    def test_dry_run_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_auto_approve_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--auto-approve"])
        assert result.exit_code == 0

    def test_demo_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--demo"])
        assert result.exit_code == 0
        assert "DEMO MODE" in result.output

    def test_log_level_option(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--log-level", "DEBUG", "run"])
        assert result.exit_code == 0
