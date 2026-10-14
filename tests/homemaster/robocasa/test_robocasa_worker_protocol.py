"""RoboCasa worker protocol validation tests."""

from __future__ import annotations

import pytest

from homemaster.robocasa.worker_protocol import (
    OPERATIONS,
    PROTOCOL,
    RoboCasaWorkerProtocolError,
    WorkerResponse,
    request_frame,
)


def _frame(**overrides):
    frame = {
        "protocol": PROTOCOL,
        "request_id": "r1",
        "status": "ok",
        "external_return_code": 0,
        "backend_attempted": False,
        "result": {},
        "error": None,
    }
    frame.update(overrides)
    return frame


def test_response_round_trip():
    response = WorkerResponse.from_json(_frame(result={"a": 1}))
    assert response.request_id == "r1"
    assert response.status == "ok"
    assert response.result == {"a": 1}


def test_ready_and_closed_statuses_accepted():
    assert WorkerResponse.from_json(_frame(status="ready")).status == "ready"
    assert WorkerResponse.from_json(_frame(status="closed")).status == "closed"


@pytest.mark.parametrize(
    "bad",
    [
        "notadict",
        _frame(protocol="other"),
        _frame(request_id=""),
        _frame(status="bogus"),
        _frame(external_return_code="x"),
        _frame(external_return_code=True),
        _frame(backend_attempted="yes"),
        _frame(result=[]),
        _frame(error="oops"),
    ],
)
def test_invalid_frames_rejected(bad):
    with pytest.raises(RoboCasaWorkerProtocolError):
        WorkerResponse.from_json(bad)


def test_request_frame_validation():
    frame = request_frame("r1", "skill", {"a": 1})
    assert frame["protocol"] == PROTOCOL
    assert frame["operation"] == "skill"
    with pytest.raises(ValueError):
        request_frame("", "skill", {})
    with pytest.raises(ValueError):
        request_frame("r1", "nope", {})
    with pytest.raises(TypeError):
        request_frame("r1", "skill", [])


def test_operations_cover_lifecycle():
    assert {
        "start",
        "reconfigure",
        "observe",
        "skill",
        "check_success",
        "close",
    } <= OPERATIONS
    # Episode reset is deliberately absent: matched-scene evaluation forbids
    # restarting a scored episode.
    assert "reset" not in OPERATIONS


def test_reset_frame_is_rejected():
    with pytest.raises(ValueError):
        request_frame("r1", "reset", {})
