"""Tests for the slash-command registry, ``!`` execution, and prompt completion."""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from prompt_toolkit.document import Document
from prompt_toolkit.keys import Keys
from rich.console import Console

from homemaster.cli import shell_commands
from homemaster.cli.keymap import build_key_bindings
from homemaster.cli.prompt_loop import (
    ShellCompleter,
    build_at_completion,
    extract_at_token,
    iter_path_entries,
)
from homemaster.cli.shell_commands import (
    REGISTRY,
    ShellContext,
    dispatch_slash_command,
    echo_unknown_command_hint,
    find_command,
    interpolate_bang_output,
    render_help,
    run_local_command,
    slash_command,
    suggest_command,
)


def _ctx(captured: list[str]) -> ShellContext:
    ctx = ShellContext(
        application=None,
        runner=None,
        bundle=SimpleNamespace(trace_path="trace.jsonl"),
        session_id="s-1",
        echo=captured.append,
    )
    return ctx


def test_registry_entries_are_complete_and_help_covers_every_command() -> None:
    assert REGISTRY, "registry must contain the builtin commands"
    names = set()
    for spec in REGISTRY:
        assert spec.name and spec.name == spec.name.strip()
        assert spec.name not in names
        names.add(spec.name)
        assert callable(spec.run)
        assert spec.help, f"/{spec.name} must declare help text"

    help_text = render_help()
    for spec in REGISTRY:
        assert f"/{spec.name}" in help_text
        assert spec.help in help_text


def test_help_output_includes_key_hints_and_bang_usage() -> None:
    help_text = render_help()
    assert "Ctrl+J" in help_text
    assert "!<cmd>" in help_text
    assert "@<path>" in help_text


def test_find_command_hits_name_and_aliases() -> None:
    assert find_command("exit") is find_command("quit")
    assert find_command("exit") is find_command("q")
    assert find_command("help") is find_command("?")
    assert find_command("nope") is None


def test_dispatch_runs_registered_command_and_alias() -> None:
    captured: list[str] = []
    ctx = _ctx(captured)

    assert dispatch_slash_command("/events", ctx) is True
    assert captured == ["Trace: trace.jsonl"]

    ctx2 = _ctx(captured)
    assert dispatch_slash_command("/quit", ctx2) is True
    assert ctx2.exit_reason == "user_exit"


def test_dispatch_returns_false_for_unknown_slash_input() -> None:
    captured: list[str] = []
    ctx = _ctx(captured)
    assert dispatch_slash_command("/definitely-not-a-command", ctx) is False
    assert dispatch_slash_command("plain text", ctx) is False


def test_unknown_command_hint_suggests_closest_match() -> None:
    captured: list[str] = []
    ctx = _ctx(captured)
    echo_unknown_command_hint("statsu", ctx)
    assert captured == ["Unknown command /statsu. Did you mean /status?"]

    captured.clear()
    echo_unknown_command_hint("zzzzzz", ctx)
    assert captured == ["Unknown command /zzzzzz. Type /help for the command list."]


def test_suggest_command_uses_aliases_pool() -> None:
    assert suggest_command("exti") in {"exit", "quit", "q"}


def test_new_builtin_commands_match_legacy_set() -> None:
    names = {spec.name for spec in REGISTRY}
    for expected in {
        "new",
        "compact",
        "status",
        "events",
        "doctor",
        "debug",
        "help",
        "exit",
    }:
        assert expected in names


def test_slash_command_decorator_registers_spec() -> None:
    calls: list[str] = []

    @slash_command("zz-temp-test", aliases=("zzt",), usage="<x>", help="temp spec.")
    def _temp(ctx, args) -> None:
        calls.append(args)

    try:
        spec = find_command("zz-temp-test")
        assert spec is not None
        assert spec.aliases == ("zzt",)
        assert spec.usage == "<x>"
        ctx = _ctx([])
        assert dispatch_slash_command("/zzt hello world", ctx) is True
        assert calls == ["hello world"]
    finally:
        REGISTRY[:] = [spec for spec in REGISTRY if spec.name != "zz-temp-test"]


def test_interpolate_bang_output_inlines_stdout(capsys) -> None:
    expanded = interpolate_bang_output("result is {!echo hello-bang} end")
    assert expanded == "result is hello-bang end"


def test_interpolate_bang_output_routes_stderr_away(capsys) -> None:
    expanded = interpolate_bang_output("{!echo out; echo err 1>&2}")
    assert expanded == "out"
    assert "err" in capsys.readouterr().err


def test_interpolate_bang_output_reports_nonzero_exit(capsys) -> None:
    expanded = interpolate_bang_output("x {!exit 3} y")
    assert expanded == "x  y"
    assert "exited with code 3" in capsys.readouterr().err


def _capture_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False), buffer


def test_run_local_command_streams_output_and_reports_exit() -> None:
    console, buffer = _capture_console()
    code = run_local_command("echo hello-local", console=console)
    assert code == 0
    output = buffer.getvalue()
    assert "$ echo hello-local" in output
    assert "hello-local" in output


def test_run_local_command_reports_nonzero_exit() -> None:
    console, buffer = _capture_console()
    code = run_local_command("exit 4", console=console)
    assert code == 4
    assert "exit 4" in buffer.getvalue()


def test_run_local_command_escapes_markup_in_command_header() -> None:
    # A `!` line whose text looks like console markup must render literally.
    console, buffer = _capture_console()
    code = run_local_command("true # [i]not-markup[/i]", console=console)
    assert code == 0
    assert "$ true # [i]not-markup[/i]" in buffer.getvalue()


def test_run_local_command_kills_process_past_timeout(monkeypatch) -> None:
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_TIMEOUT_S", 0.3)
    console, buffer = _capture_console()
    start = time.monotonic()
    code = run_local_command(
        f'"{sys.executable}" -c "import time; time.sleep(30)"',
        console=console,
    )
    elapsed = time.monotonic() - start
    # The assertion is the point: the call must return long before sleep(30).
    assert elapsed < 15
    assert code != 0
    assert "[timed out]" in buffer.getvalue()


def test_run_local_command_truncates_unbounded_output(monkeypatch) -> None:
    # `yes`-style infinite writer; the test completing at all proves the cap
    # kills the producer instead of hanging the prompt loop.
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_OUTPUT_MAX_LINES", 50)
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_OUTPUT_LIMIT", 4096)
    console, buffer = _capture_console()
    code = run_local_command(
        f'"{sys.executable}" -c '
        "\"import itertools, sys; sys.stdout.writelines(itertools.repeat('x\\n'))\"",
        console=console,
    )
    assert code != 0
    assert "[truncated]" in buffer.getvalue()


def test_run_local_command_terminal_panel_marks_truncated(monkeypatch) -> None:
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_OUTPUT_MAX_LINES", 20)
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_OUTPUT_LIMIT", 4096)
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=100)
    code = run_local_command(
        f'"{sys.executable}" -c '
        "\"import itertools, sys; sys.stdout.writelines(itertools.repeat('x\\n'))\"",
        console=console,
    )
    assert code != 0
    assert "[truncated]" in buffer.getvalue()


def test_interpolate_bang_output_truncates_long_stdout(monkeypatch) -> None:
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_OUTPUT_LIMIT", 64)
    expanded = interpolate_bang_output(f'pre {{!"{sys.executable}" -c "print(\'x\' * 200)"}} post')
    assert expanded is not None
    assert expanded.startswith("pre " + "x" * 64)
    assert "[truncated]" in expanded
    assert len(expanded) < 200


def test_interpolate_bang_output_timeout_returns_none(monkeypatch, capsys) -> None:
    monkeypatch.setattr(shell_commands, "_LOCAL_CMD_TIMEOUT_S", 0.3)
    result = interpolate_bang_output(
        f'x {{!"{sys.executable}" -c "import time; time.sleep(30)"}} y'
    )
    assert result is None
    assert "timed out" in capsys.readouterr().err


def test_ctrl_c_binding_clears_buffer_instead_of_exiting() -> None:
    bindings = build_key_bindings()
    ctrl_c = [b for b in bindings.bindings if Keys.ControlC in b.keys]
    assert ctrl_c, "expected a Ctrl+C binding"
    buffer = SimpleNamespace(reset=Mock())
    for binding in ctrl_c:
        binding.handler(SimpleNamespace(current_buffer=buffer))
    assert buffer.reset.call_count == len(ctrl_c)


def test_extract_at_token_plain_quoted_and_none() -> None:
    assert extract_at_token("hello @src/ma") == ("@src/ma", "src/ma", False)
    assert extract_at_token('@my "quoted') is None
    assert extract_at_token('x @"dir/file') == ('@"dir/file', "dir/file", True)
    assert extract_at_token("email a@b.com") is None
    assert extract_at_token("no token") is None


def test_iter_path_entries_matches_and_marks_dirs(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "alpine").mkdir()
    (tmp_path / "beta.txt").write_text("b")
    (tmp_path / ".hidden").write_text("h")

    entries = list(iter_path_entries("al", str(tmp_path)))
    assert {(insert, display, is_dir) for insert, display, is_dir in entries} == {
        ("alpha.txt", "alpha.txt", False),
        ("alpine/", "alpine/", True),
    }

    hidden = list(iter_path_entries(".h", str(tmp_path)))
    assert [display for _, display, _ in hidden] == [".hidden"]

    nested = list(iter_path_entries("alpine/", str(tmp_path)))
    assert nested == []


def test_iter_path_entries_nested_and_home(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "leaf.txt").write_text("x")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "inside.md").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    nested = list(iter_path_entries("dir/le", str(tmp_path)))
    assert [(insert, display) for insert, display, _ in nested] == [("dir/leaf.txt", "leaf.txt")]

    home = list(iter_path_entries("~/in", str(tmp_path)))
    assert [(insert, display) for insert, display, _ in home] == [("~/inside.md", "inside.md")]

    absolute = list(iter_path_entries(str(tmp_path) + "/be", str(tmp_path)))
    assert absolute == []


def test_build_at_completion_quoting_and_dir_continuation() -> None:
    assert build_at_completion("dir/", is_dir=True, quoted=False) == "@dir/"
    assert build_at_completion("a b/", is_dir=True, quoted=False) == '@"a b/'
    assert build_at_completion("f.txt", is_dir=False, quoted=False) == "@f.txt "
    assert build_at_completion("f.txt", is_dir=False, quoted=True) == '@"f.txt" '
    assert build_at_completion("my file.txt", is_dir=False, quoted=False) == '@"my file.txt" '


def test_completer_suggests_slash_commands() -> None:
    completer = ShellCompleter(base_path="/nonexistent")
    items = list(completer.get_completions(Document("/st"), None))
    texts = {item.text for item in items}
    assert "/status " in texts
    assert all(item.start_position == -3 for item in items)


def test_completer_suggests_at_paths(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("x")
    (tmp_path / "sub dir").mkdir()
    completer = ShellCompleter(base_path=str(tmp_path))
    items = list(completer.get_completions(Document("read @no please"), None))
    # cursor is at the end of the document; build document with cursor mid-text
    document = Document("read @no", cursor_position=len("read @no"))
    items = list(completer.get_completions(document, None))
    assert [item.text for item in items] == ["@notes.md "]
    assert items[0].start_position == -len("@no")

    document = Document("@s", cursor_position=2)
    items = list(completer.get_completions(document, None))
    assert [item.text for item in items] == ['@"sub dir/']
    assert items[0].display_text == "sub dir/"


def test_completer_uses_arg_completer_for_command_args() -> None:
    completer = ShellCompleter(base_path="/nonexistent")
    document = Document("/help sta", cursor_position=len("/help sta"))
    items = list(completer.get_completions(document, None))
    assert [item.text for item in items] == ["status"]


def test_help_line_format_matches_legacy_shape() -> None:
    help_text = render_help()
    assert "/compact: persist an immediate context compaction." in help_text
    assert "/status: show typed application session status." in help_text
