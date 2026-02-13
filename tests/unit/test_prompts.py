"""Unit tests for Rich prompts."""

from __future__ import annotations

from workflow_conductor.ui.prompts import prompt_validation_gate


class TestPromptValidationGate:
    def test_auto_approve_returns_approve(self) -> None:
        response = prompt_validation_gate(auto_approve=True)
        assert response.action == "approve"
        assert response.feedback == ""
