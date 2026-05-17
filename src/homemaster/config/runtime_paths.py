"""Path-safety helpers for runtime artifacts."""

from __future__ import annotations

import re


class InvalidRunIdError(ValueError):
    """Raised when run_id contains path-traversal or invalid characters."""


_VALID_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_run_id(run_id: str) -> str:
    """Validate run_id is safe for use as a path component.

    Rejects: empty, path separators (/ \\), .. and ., absolute paths,
    control characters, values > 128 chars.
    Returns the validated run_id unchanged.
    """
    if not isinstance(run_id, str) or not run_id:
        raise InvalidRunIdError("run_id must be a non-empty string")
    if "/" in run_id or "\\" in run_id:
        raise InvalidRunIdError(
            f"run_id must not contain path separators: {run_id!r}"
        )
    if run_id in (".", ".."):
        raise InvalidRunIdError("run_id must not be '.' or '..'")
    if run_id.startswith("/"):
        raise InvalidRunIdError(
            f"run_id must not be an absolute path: {run_id!r}"
        )
    if not _VALID_RUN_ID.match(run_id):
        raise InvalidRunIdError(
            f"run_id must match [A-Za-z0-9][A-Za-z0-9._-]*: {run_id!r}"
        )
    return run_id
