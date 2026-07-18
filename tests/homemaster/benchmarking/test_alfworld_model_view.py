from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.benchmarking.alfworld.model_view import (
    AlfworldModelViewObserver,
    FrameLedger,
)
from homemaster.providers.attempts import (
    OutboundImageBinding,
    ProviderAttemptRecord,
)


def _png(path: Path, value: int) -> None:
    image = pytest.importorskip("PIL.Image")
    image.new("RGB", (3, 3), (value, value, value)).save(path)


def test_model_view_commits_last_bound_image_by_message_and_block_order(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _png(first, 10)
    _png(second, 20)
    ledger = FrameLedger()
    first_record = ledger.record_frame(first, event_sequence=1)
    second_record = ledger.record_frame(second, event_sequence=2)
    observer = AlfworldModelViewObserver(frame_ledger=ledger)
    messages = [
        UserMessage(
            content=[
                ContentBlock.from_image_path(first),
                ContentBlock(text="then"),
                ContentBlock.from_image_path(second),
            ]
        )
    ]

    committed = observer.commit_from_messages(
        messages,
        model_attempt_id="attempt-1",
        request_sha256="a" * 64,
    )

    assert committed.frame_binding_id == second_record.frame_binding_id
    assert committed.frame_binding_id != first_record.frame_binding_id
    assert committed.event_sequence == 2


def test_identical_pixels_at_new_event_do_not_advance_view_without_commit(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    duplicate = tmp_path / "duplicate.png"
    _png(first, 42)
    _png(duplicate, 42)
    ledger = FrameLedger()
    first_record = ledger.record_frame(first, event_sequence=3)
    observer = AlfworldModelViewObserver(frame_ledger=ledger)
    observer.commit_from_messages(
        [UserMessage(content=[ContentBlock.from_image_path(first)])],
        model_attempt_id="attempt-1",
        request_sha256="b" * 64,
    )

    duplicate_record = ledger.record_frame(duplicate, event_sequence=4)

    assert observer.current_view is not None
    assert observer.current_view.frame_binding_id == first_record.frame_binding_id
    assert duplicate_record.frame_binding_id != first_record.frame_binding_id


def test_successful_attempt_commits_exact_request_and_last_binding(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    duplicate = tmp_path / "duplicate.png"
    _png(first, 33)
    _png(duplicate, 33)
    ledger = FrameLedger()
    first_record = ledger.record_frame(first, event_sequence=7)
    duplicate_record = ledger.record_frame(duplicate, event_sequence=8)
    observer = AlfworldModelViewObserver(frame_ledger=ledger)
    messages = observer.bind_messages(
        [
            UserMessage(
                content=[
                    ContentBlock.from_image_path(first),
                    ContentBlock.from_image_path(duplicate),
                ]
            )
        ]
    )
    blocks = messages[0].content
    request_sha256 = "c" * 64
    attempt = ProviderAttemptRecord(
        model_attempt_id="attempt-exact",
        request_sha256=request_sha256,
        outbound_images=tuple(
            OutboundImageBinding(
                message_index=0,
                block_index=index,
                frame_binding_id=str(block.metadata["frame_binding_id"]),
                content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for index, (block, path) in enumerate(
                zip(blocks, (first, duplicate), strict=True)
            )
        ),
        stripped_images=False,
        response_completed=True,
        error_type=None,
        cause_code=None,
    )

    committed = observer.commit_successful_response(attempt=attempt)

    assert committed.request_sha256 == request_sha256
    assert committed.frame_binding_id == duplicate_record.frame_binding_id
    assert committed.frame_binding_id != first_record.frame_binding_id
    assert committed.event_sequence == 8
