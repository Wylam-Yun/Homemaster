from __future__ import annotations

import base64
import hashlib
import json

import pytest

from homemaster.tools.contracts import (
    ObservationReference,
    OutcomeCertainty,
    ResultAttachment,
    ResultImage,
    TerminalInfo,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationRecord,
    VerificationStatus,
)


def _encoded(value: bytes) -> tuple[str, str]:
    return base64.b64encode(value).decode("ascii"), hashlib.sha256(value).hexdigest()


def test_full_result_projects_losslessly_to_tool_result_message() -> None:
    image_data, image_sha = _encoded(b"png-image-bytes")
    attachment_data, attachment_sha = _encoded(b"attachment-bytes")
    result = ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text="completed",
        data={"receipt": {"id": "r-1"}, "items": [1, 2]},
        images=(
            ResultImage(
                media_type="image/png",
                data_base64=image_data,
                content_sha256=image_sha,
                pixel_sha256="a" * 64,
                observation_id="obs-1",
            ),
        ),
        attachments=(
            ResultAttachment(
                filename="receipt.json",
                media_type="application/json",
                data_base64=attachment_data,
                content_sha256=attachment_sha,
            ),
        ),
        observations=(
            ObservationReference(
                observation_id="obs-1",
                evidence_ref="observations/obs-1.json",
                content_sha256=image_sha,
            ),
        ),
        evidence_refs=("ledger/tool-call-1.json",),
        outcome_certainty=OutcomeCertainty.CONFIRMED,
        verification=VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="external state matched",
            evidence_refs=("verification/tool-call-1.json",),
        ),
        terminal=TerminalInfo(
            classification="agent_success",
            score_eligible=True,
            evidence_ref="terminal/tool-call-1.json",
        ),
        backend_attempted=True,
    )

    message = result.to_message(tool_call_id="call-1", name="robot_go_to")
    assert message.tool_call_id == "call-1"
    assert message.name == "robot_go_to"
    assert message.is_error is False
    assert message.data == result.to_dict()
    assert json.loads(message.content[0].text) == result.to_dict()
    assert message.content[1].type == "image"
    assert message.content[1].source == {
        "type": "base64",
        "media_type": "image/png",
        "data": image_data,
    }
    assert message.content[1].metadata["observation_id"] == "obs-1"
    assert message.data["attachments"][0]["data_base64"] == attachment_data
    assert message.data["terminal"]["classification"] == "agent_success"


def test_result_data_is_deeply_immutable_and_serializable() -> None:
    source = {"nested": {"items": [1, 2]}}
    result = ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data=source)
    source["nested"]["items"].append(3)
    assert result.data["nested"]["items"] == (1, 2)
    with pytest.raises(TypeError):
        result.data["nested"]["items"] = ()
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()


@pytest.mark.parametrize(
    ("values", "error"),
    [
        (
            {
                "status": ToolExecutionStatus.SUCCESS,
                "error": ToolExecutionError("x", "x"),
            },
            "successful",
        ),
        ({"status": ToolExecutionStatus.FAILURE}, "requires a typed error"),
        (
            {
                "status": ToolExecutionStatus.DENIED,
                "error": ToolExecutionError("permission_denied", "denied"),
                "retryable": True,
            },
            "only a confirmed failure",
        ),
        (
            {
                "status": ToolExecutionStatus.OUTCOME_UNKNOWN,
                "error": ToolExecutionError("transport_lost", "unknown"),
                "outcome_certainty": OutcomeCertainty.UNKNOWN,
            },
            "attempted backend",
        ),
        (
            {
                "status": ToolExecutionStatus.FAILURE,
                "error": ToolExecutionError("failed", "failed"),
                "outcome_certainty": OutcomeCertainty.UNKNOWN,
            },
            "requires status=outcome_unknown",
        ),
        (
            {
                "status": ToolExecutionStatus.INVALID,
                "error": ToolExecutionError("invalid", "invalid"),
                "backend_attempted": True,
            },
            "cannot claim a backend attempt",
        ),
        (
            {
                "status": ToolExecutionStatus.FAILURE,
                "error": ToolExecutionError("failed", "failed"),
                "verification": VerificationRecord(
                    status=VerificationStatus.PASSED,
                    evidence_refs=("verification/passed.json",),
                ),
            },
            "passed verification",
        ),
        (
            {
                "status": ToolExecutionStatus.SUCCESS,
                "verification": VerificationRecord(
                    status=VerificationStatus.FAILED,
                    evidence_refs=("verification/failed.json",),
                ),
            },
            "failed verification",
        ),
        (
            {
                "status": ToolExecutionStatus.SUCCESS,
                "verification": VerificationRecord(status=VerificationStatus.PENDING),
            },
            "pending verification requires",
        ),
        (
            {
                "status": ToolExecutionStatus.OBSERVATION_REQUIRED,
                "error": ToolExecutionError("observation_required", "observe first"),
                "terminal": TerminalInfo("agent_model_failure", True),
            },
            "terminal information",
        ),
        (
            {
                "status": ToolExecutionStatus.CANCELLED,
                "error": ToolExecutionError("cancelled", "cancelled"),
                "backend_attempted": True,
            },
            "cannot claim a backend attempt",
        ),
        (
            {
                "status": ToolExecutionStatus.SUCCESS,
                "evidence_refs": "not-a-sequence",
            },
            "sequence of strings",
        ),
    ],
)
def test_result_rejects_illegal_status_combinations(values, error: str) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        ToolExecutionResult(**values)


def test_outcome_unknown_is_non_retryable_and_preserves_failure_fields() -> None:
    result = ToolExecutionResult(
        status=ToolExecutionStatus.OUTCOME_UNKNOWN,
        error=ToolExecutionError(
            code="transport_lost",
            message="backend result could not be confirmed",
            details={"attempt_id": "a-1"},
        ),
        outcome_certainty=OutcomeCertainty.UNKNOWN,
        backend_attempted=True,
    )
    assert result.success is False
    assert result.retryable is False
    assert result.failure_reason == "backend result could not be confirmed"
    assert result.to_message(tool_call_id="call-1", name="move").is_error is True


def test_media_and_attachment_hashes_are_verified_at_construction() -> None:
    encoded, digest = _encoded(b"payload")
    with pytest.raises(ValueError, match="hash mismatch"):
        ResultImage("image/png", encoded, "0" * 64)
    with pytest.raises(ValueError, match="hash mismatch"):
        ResultAttachment("result.json", "application/json", encoded, "0" * 64)
    with pytest.raises(ValueError, match="must not contain a path"):
        ResultAttachment("../result.json", "application/json", encoded, digest)


def test_verification_pending_requires_matching_typed_status() -> None:
    result = ToolExecutionResult(
        status=ToolExecutionStatus.VERIFICATION_PENDING,
        error=ToolExecutionError("verification_pending", "verification has not completed"),
        verification=VerificationRecord(status=VerificationStatus.PENDING),
    )
    assert result.verification.status is VerificationStatus.PENDING
