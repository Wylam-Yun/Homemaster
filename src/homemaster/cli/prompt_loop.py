"""prompt_toolkit-backed input layer for the interactive shell.

Replaces the plain ``input()`` loop when stdin/stdout are TTYs. Provides
persistent history (``$HOMEMASTER_HOME/shell_history``), multiline input
(Enter submits, Ctrl+J/Alt+Enter inserts a newline), slash-command and
``@`` path completion, a bash-mode prompt hint for ``!`` lines, and a
bottom toolbar with model/context/cwd.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from homemaster.cli.keymap import build_key_bindings
from homemaster.cli.shell_commands import REGISTRY, CommandSpec, find_command


def _default_history_path() -> Path:
    """Shell history anchored at ``$HOMEMASTER_HOME`` (default ``~/.homemaster``)."""

    configured = os.environ.get("HOMEMASTER_HOME", "").strip()
    if configured:
        return Path(configured).expanduser() / "shell_history"
    home = os.environ.get("HOME", "").strip()
    if home:
        return Path(home) / ".homemaster" / "shell_history"
    return Path("~/.homemaster/shell_history").expanduser()


HISTORY_PATH = _default_history_path()

_TOKEN_START_CHARS = " \t"
_QUOTE_NEEDED_CHARS = frozenset(" \t\"'()[]{}<>|;&$`\\")


def interactive_prompt_supported() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _unclosed_quote_start(text: str) -> int | None:
    in_quotes = False
    start = -1
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
            if in_quotes:
                start = index
    return start if in_quotes else None


def _is_token_start(text: str, index: int) -> bool:
    return index == 0 or text[index - 1] in _TOKEN_START_CHARS


def extract_at_token(text: str) -> tuple[str, str, bool] | None:
    """Return (token, raw_path_prefix, quoted) for an ``@`` path token at the
    end of ``text``, or None when the cursor is not inside such a token."""

    quote_start = _unclosed_quote_start(text)
    if quote_start is not None:
        if (
            quote_start > 0
            and text[quote_start - 1] == "@"
            and _is_token_start(text, quote_start - 1)
        ):
            token = text[quote_start - 1 :]
            return token, token[2:], True
        return None
    start = max(text.rfind(" "), text.rfind("\t")) + 1
    token = text[start:]
    if token.startswith("@"):
        return token, token[1:], False
    return None


def _expand_search_dir(dir_raw: str, base_path: str) -> str:
    if dir_raw in {"", "."}:
        return base_path
    if dir_raw.startswith("~"):
        return os.path.expanduser(dir_raw)
    if os.path.isabs(dir_raw):
        return dir_raw
    return os.path.join(base_path, dir_raw)


def iter_path_entries(raw_prefix: str, base_path: str) -> Iterable[tuple[str, str, bool]]:
    """Yield (insert_text_after_@, display_name, is_dir) for a raw path prefix."""

    if raw_prefix.endswith("/"):
        dir_raw, fragment = raw_prefix, ""
    else:
        dir_part, _, fragment = raw_prefix.rpartition("/")
        dir_raw = f"{dir_part}/" if dir_part else ""
    search_dir = _expand_search_dir(dir_raw, base_path)
    try:
        entries = sorted(os.scandir(search_dir), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if name.startswith(".") and not fragment.startswith("."):
            continue
        if not name.casefold().startswith(fragment.casefold()):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        suffix = "/" if is_dir else ""
        yield dir_raw + name + suffix, name + suffix, is_dir


def build_at_completion(insert_text: str, is_dir: bool, quoted: bool) -> str:
    """Build the full ``@`` replacement text for one completion entry."""

    needs_quotes = quoted or any(char in _QUOTE_NEEDED_CHARS for char in insert_text)
    if is_dir:
        if needs_quotes:
            return f'@"{insert_text}'
        return f"@{insert_text}"
    if needs_quotes:
        return f'@"{insert_text}" '
    return f"@{insert_text} "


class ShellCompleter(Completer):
    """Merged completer: ``/`` commands (with argument hints) and ``@`` paths."""

    def __init__(
        self,
        *,
        commands: Iterable[CommandSpec] = REGISTRY,
        base_path: str | Callable[[], str] = os.getcwd,
    ) -> None:
        self._commands = list(commands)
        self._base_path = base_path

    def _base(self) -> str:
        return self._base_path() if callable(self._base_path) else self._base_path

    def get_completions(self, document: Document, complete_event):
        del complete_event
        line = document.text_before_cursor.rsplit("\n", 1)[-1]

        at_token = extract_at_token(line)
        if at_token is not None:
            token, raw_prefix, quoted = at_token
            for insert, display, is_dir in iter_path_entries(raw_prefix, self._base()):
                yield Completion(
                    build_at_completion(insert, is_dir, quoted),
                    start_position=-len(token),
                    display=display,
                    display_meta="dir" if is_dir else "file",
                )
            return

        stripped = line.lstrip()
        if not stripped.startswith("/"):
            return
        if " " not in stripped:
            prefix = stripped[1:]
            for spec in self._commands:
                for name in (spec.name, *spec.aliases):
                    if name.startswith(prefix):
                        yield Completion(
                            f"/{name} ",
                            start_position=-len(stripped),
                            display=f"/{name}",
                            display_meta=spec.help,
                        )
            return
        command_name = stripped[1:].split(" ", 1)[0]
        spec = find_command(command_name, self._commands)
        if spec is None or spec.arg_completer is None:
            return
        arg_text = stripped.split(" ", 1)[1]
        arg_prefix = arg_text.rsplit(" ", 1)[-1]
        for value, meta in spec.arg_completer(arg_prefix):
            yield Completion(
                value,
                start_position=-len(arg_prefix),
                display=value,
                display_meta=meta,
            )


class ShellPrompt:
    """Thin wrapper around PromptSession with HomeMaster wiring."""

    def __init__(
        self,
        *,
        model_name: Callable[[], str | None] | str | None = None,
        context_usage: Callable[[], str | None] | str | None = None,
        ui_mode: Callable[[], str | None] | str | None = None,
        on_toggle_mode: Callable[[], None] | None = None,
        commands: Iterable[CommandSpec] = REGISTRY,
        history_path: str | Path | None = None,
        base_path: str | Callable[[], str] = os.getcwd,
    ) -> None:
        self._model_name = model_name
        self._context_usage = context_usage
        self._ui_mode = ui_mode
        history_path = Path(history_path) if history_path is not None else _default_history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_path)),
            completer=ShellCompleter(commands=commands, base_path=base_path),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            multiline=True,
            prompt_continuation="… ",
            key_bindings=build_key_bindings(on_toggle_mode=on_toggle_mode),
            bottom_toolbar=self._render_toolbar,
            style=Style.from_dict(
                {
                    "prompt": "ansicyan bold",
                    "prompt.bash": "ansigreen bold",
                    "prompt.plan": "ansiyellow bold",
                    "bottom-toolbar": "bg:#333333 #eeeeee",
                    "bottom-toolbar.mode": "bg:#5f3300 #ffffff",
                    "completion-menu.completion.current": "bg:#00aaaa #000000",
                }
            ),
        )

    @staticmethod
    def _resolve(source: Callable[[], str | None] | str | None) -> str:
        value = source() if callable(source) else source
        return value if value else "--"

    def _mode(self) -> str | None:
        value = self._ui_mode() if callable(self._ui_mode) else self._ui_mode
        return value or None

    def _prompt_message(self) -> AnyFormattedText:
        app = get_app_or_none()
        text = app.current_buffer.text if app is not None else ""
        if text.lstrip().startswith("!"):
            return [("class:prompt.bash", "!"), ("class:prompt", self._prompt_label())]
        style = "class:prompt.plan" if self._mode() == "plan" else "class:prompt"
        return [(style, self._prompt_label())]

    def _prompt_label(self) -> str:
        mode = self._mode()
        if mode == "plan":
            return "homemaster(plan)> "
        if mode == "act":
            return "homemaster(act)> "
        return "homemaster> "

    def _render_toolbar(self) -> AnyFormattedText:
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        if cwd == home or cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home) :]
        model = self._resolve(self._model_name)
        context_usage = self._resolve(self._context_usage)
        mode = self._mode()
        fragments: list[tuple[str, str]] = [
            ("class:bottom-toolbar", f" model={model} | ctx={context_usage}"),
        ]
        if mode:
            fragments.append(("class:bottom-toolbar.mode", f" {mode.upper()} "))
        fragments.append(("class:bottom-toolbar", f" | {cwd} "))
        return fragments

    def read(self) -> str:
        return self._session.prompt(self._prompt_message)

    async def read_async(self) -> str:
        """In-loop prompt for the remote shell — keeps WS/stdout tasks alive.

        ``patch_stdout`` routes prints from other tasks above the prompt.
        """

        from prompt_toolkit.patch_stdout import patch_stdout

        with patch_stdout():
            return await self._session.prompt_async(self._prompt_message)


__all__ = [
    "HISTORY_PATH",
    "ShellCompleter",
    "ShellPrompt",
    "build_at_completion",
    "extract_at_token",
    "interactive_prompt_supported",
    "iter_path_entries",
]
