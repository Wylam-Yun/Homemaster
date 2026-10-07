"""Key action table and prompt_toolkit bindings for the interactive shell."""

from __future__ import annotations

from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import has_completions, has_focus
from prompt_toolkit.key_binding import KeyBindings

# Single source of truth for shell key actions; command code must not scatter
# raw key comparisons. prompt_toolkit names are used so the table can be fed
# straight into KeyBindings.add.
ACTION_KEYS: dict[str, str | tuple[str, ...]] = {
    "interrupt": "c-c",  # clear the prompt buffer (while a run executes, SIGINT cancels it)
    "clear_input": "escape",  # wipe the current input buffer
    "newline": "c-j",  # insert a literal newline (multiline input)
    "newline_alt": ("escape", "c-m"),  # Alt+Enter also inserts a newline
    "submit": "enter",  # submit the buffer
    "history_prev": "up",  # walk prompt history backwards (restores draft)
    "history_next": "down",  # walk prompt history forwards
}


def _keys(spec: str | tuple[str, ...]) -> tuple[str, ...]:
    return (spec,) if isinstance(spec, str) else spec


KEY_HELP_LINES = (
    "Enter: submit the input.",
    "Ctrl+J or Alt+Enter: insert a newline (multiline input).",
    "Up/Down: prompt history; an unsubmitted draft is restored at the end.",
    "Esc or Ctrl+C: clear the current input; Ctrl+C during a run interrupts it.",
    "Ctrl+D or /exit: exit the shell.",
    "!<cmd>: run a local shell command (output is not sent to the model).",
    "{!<cmd>} inside input: inline the command's stdout into the sent text.",
    "@<path>: file path completion (Tab / while typing).",
)


def build_key_bindings() -> KeyBindings:
    """Return the interactive prompt key bindings driven by ACTION_KEYS."""

    bindings = KeyBindings()

    # Enter submits, but while the completion menu is visible Enter must keep
    # its default "accept selected completion" meaning.
    @bindings.add(ACTION_KEYS["submit"], filter=has_focus(DEFAULT_BUFFER) & ~has_completions)
    def submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add(*_keys(ACTION_KEYS["newline"]))
    @bindings.add(*_keys(ACTION_KEYS["newline_alt"]))
    def insert_newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add(*_keys(ACTION_KEYS["clear_input"]))
    def clear_input(event) -> None:
        event.current_buffer.reset()

    @bindings.add(*_keys(ACTION_KEYS["interrupt"]))
    def interrupt(event) -> None:
        # At the prompt Ctrl+C clears the line instead of exiting the shell;
        # quitting is Ctrl+D or /exit. While a run executes the prompt app is
        # inactive, so a real SIGINT still reaches the loop's interrupt handler.
        event.current_buffer.reset()

    return bindings


__all__ = ["ACTION_KEYS", "KEY_HELP_LINES", "build_key_bindings"]
