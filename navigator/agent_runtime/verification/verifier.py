"""Mandatory verification wrapper."""

from __future__ import annotations

from navigator.agent_runtime.models import ActionResult, AgentAction, VerificationResult
from navigator.core.schemas import VerifyResult


def build_verification(action: AgentAction, verify: VerifyResult | None) -> VerificationResult:
    passed = verify.passed if verify is not None else False
    status = "passed" if passed else ("ambiguous" if verify and verify.ambiguous else "failed")
    return VerificationResult(
        action_id=action.action_id,
        passed=passed,
        verify=verify,
        postcondition=None,
    )


def action_result_from_parts(action: AgentAction, call, result, page_id: str) -> ActionResult:
    return ActionResult(
        action_id=action.action_id,
        tool_call=call,
        tool_result=result,
        page_id=page_id,
    )
