from __future__ import annotations

import pytest

import homemaster.events.public_projection as projection_module
from homemaster.events.public_projection import PublicEventProjection


def _stream(*, secrets: tuple[str, ...] = ()):
    assert hasattr(projection_module, "StreamingPublicTextSanitizer")
    return projection_module.StreamingPublicTextSanitizer(sensitive_values=secrets)


def _stream_at_boundaries(
    text: str,
    boundaries: list[int],
    *,
    secrets: tuple[str, ...] = (),
) -> str:
    sanitizer = _stream(secrets=secrets)
    released: list[str] = []
    cursor = 0
    for boundary in boundaries:
        released.append(sanitizer.feed(text[cursor:boundary]))
        cursor = boundary
    released.append(sanitizer.feed(text[cursor:]))
    released.append(sanitizer.finish())
    return "".join(released)


def test_ordinary_prose_is_released_before_completion() -> None:
    sanitizer = _stream()

    assert sanitizer.feed("hello ") == "hello "
    assert sanitizer.feed("world") == "world"
    assert sanitizer.finish() == ""


def test_configured_secret_is_safe_at_every_character_boundary() -> None:
    secret = "configured-super-secret"
    text = f"prefix {secret} suffix"
    expected = PublicEventProjection(sensitive_values=(secret,)).sanitize_content(text)

    for boundary in range(1, len(text)):
        released = _stream_at_boundaries(text, [boundary], secrets=(secret,))
        assert secret not in released
        assert released == expected


@pytest.mark.parametrize(
    ("text", "boundaries"),
    [
        ("prefix api_key=raw-value suffix", [8, 12, 16, 20]),
        ("prefix Authorization: Bearer raw-value suffix", [8, 18, 26, 32]),
        ("prefix Bearer raw-value suffix", [8, 11, 17]),
        ("prefix /hpc2hdd/home/operator/private.txt suffix", [8, 12, 20, 30]),
        (
            "prefix https://example.test/path?token=raw#fragment suffix",
            [8, 13, 25, 34, 42, 48],
        ),
    ],
)
def test_lexical_sensitive_constructs_match_one_shot_sanitization(
    text: str,
    boundaries: list[int],
) -> None:
    expected = PublicEventProjection().sanitize_content(text)

    released = _stream_at_boundaries(text, boundaries)

    assert "raw-value" not in released
    assert "/hpc2hdd/home/operator" not in released
    assert "?token=raw" not in released
    assert released == expected


@pytest.mark.parametrize("terminal", ["failure", "cancel"])
def test_terminal_finish_never_leaks_retained_suffix_and_erases_state(terminal: str) -> None:
    del terminal
    secret = "secret-value"
    sanitizer = _stream(secrets=(secret,))

    released = sanitizer.feed("prefix secret-") + sanitizer.finish()

    assert "secret-" not in released
    assert secret not in released
    assert released == "prefix [REDACTED]"
    assert sanitizer.finish() == ""
    assert sanitizer.feed("next run text") == "next run text"


def test_sanitizer_state_is_not_shared_between_runs() -> None:
    secret = "secret-value"
    first = _stream(secrets=(secret,))
    second = _stream(secrets=(secret,))

    assert first.feed("secret-") == ""
    assert second.feed("ordinary") == "ordinary"
    assert first.feed("value ") + first.finish() == "[REDACTED] "
    assert second.finish() == ""
