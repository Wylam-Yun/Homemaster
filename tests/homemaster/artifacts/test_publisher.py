from __future__ import annotations

import base64
import hashlib

from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.tools.contracts import (
    ResultAttachment,
    ResultImage,
    ToolExecutionResult,
    ToolExecutionStatus,
)


def test_publisher_stores_raw_bytes_without_rewriting_model_result(tmp_path) -> None:
    image_bytes = b"private-image-bytes"
    attachment_bytes = b"private-attachment-bytes"
    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    attachment_base64 = base64.b64encode(attachment_bytes).decode("ascii")
    store = ToolOutputStore(tmp_path / "store", quota_bytes=4096, ttl_seconds=60)
    publisher = ArtifactPublisher(store)
    result = ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text="created artifacts",
        images=(
            ResultImage(
                media_type="image/png",
                data_base64=image_base64,
                content_sha256=hashlib.sha256(image_bytes).hexdigest(),
            ),
        ),
        attachments=(
            ResultAttachment(
                filename="report.bin",
                media_type="application/octet-stream",
                data_base64=attachment_base64,
                content_sha256=hashlib.sha256(attachment_bytes).hexdigest(),
            ),
        ),
    )

    artifacts = publisher.publish(
        result,
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    )

    assert result.images[0].data_base64 == image_base64
    assert result.attachments[0].data_base64 == attachment_base64
    assert len(artifacts) == 2
    assert [item["filename"] for item in artifacts] == ["image-0.png", "report.bin"]
    for item, expected in zip(artifacts, (image_bytes, attachment_bytes), strict=True):
        assert item["artifact_handle"].startswith("hm-artifact:")
        assert (
            store.read(
                item["artifact_handle"],
                tenant_id="tenant-a",
                session_id="session-a",
                run_id="run-a",
            )
            == expected
        )
