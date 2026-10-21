"""Interactive credential wizard behind ``homemaster auth`` (W3-1).

Walks the operator through provider-template selection, endpoint/model/key
entry and a masked-key confirmation page, then writes the chat-provider
profile into ``$HOMEMASTER_HOME/config.yaml`` with mode 0600 — the same
shape ``hermes model`` / openclaw onboard produce.

Entry point for ``cli/app.py`` wiring (the command registration lives in
app.py and is not part of this module)::

    from homemaster.cli.auth import auth_command

    @app.command("auth")
    def auth_command_entry(
        print_only: Annotated[bool, typer.Option("--print-only")] = False,
        config_path: Annotated[Path | None, typer.Option("--config")] = None,
    ) -> None:
        raise typer.Exit(code=auth_command(print_only=print_only, config_path=config_path))

``auth_command`` returns a process exit code (0 ok, 1 aborted/invalid,
130 on Ctrl-C) so the app wrapper only has to raise ``typer.Exit``.

Write semantics
---------------
* Target path: explicit ``--config`` > ``$HOMEMASTER_HOME/config.yaml`` >
  ``~/.homemaster/config.yaml`` (same resolution as ``_homemaster_home``).
* Existing file: YAML merge that only rewrites the ``providers`` section
  (append or replace the named item, optionally ``providers.default``) plus
  ``runtime_defaults.default_provider_name`` when the user opts into making
  the provider the default — that key, not ``providers.default``, is what
  the runtime actually resolves (``application/factory.py``).  Every other
  top-level key is preserved by value; comments/formatting are not (the
  project standard YAML library is PyYAML, not ruamel).
* Missing file: a minimal complete document (``providers`` +
  ``runtime_defaults`` + ``memory.mode: files``); every other
  ``HomeMasterConfig`` section defaults in code.
* Headless: ``--print-only`` or a non-TTY stdin prints the YAML snippet and
  the target path instead of prompting or writing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import typer
import yaml
from typer import confirm as _typer_confirm

from homemaster.config.config import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_PROVIDER_NAME,
    ConfigError,
)
from homemaster.services.provider_config import (
    _TEMPLATE_DEFAULTS,
    ProviderAnswers,
    ProviderTemplate,
    _existing_provider_names,
    _load_existing_payload,
    build_provider_item,
    env_override_hint,
    mask_api_key,
    merge_providers_section,
    minimal_config_payload,
    provider_templates,
    resolve_auth_config_path,
    write_auth_config,
)


def render_print_only_output(target: Path, *, target_exists: bool) -> str:
    """Headless output: YAML snippet + where it goes.  Nothing is written."""

    placeholder = ProviderAnswers(
        name=DEFAULT_PROVIDER_NAME,
        api_format="anthropic",
        transport="anthropic_sdk",
        auth_type="api_key",
        base_url=_TEMPLATE_DEFAULTS["anthropic"]["base_url"],
        model=_TEMPLATE_DEFAULTS["anthropic"]["model"],
        api_key="<your-api-key>",
        context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
        set_default=True,
    )
    lines = [
        "homemaster auth — non-interactive mode",
        f"target config: {target}",
        "",
        "No interactive terminal detected (or --print-only was given); nothing was written.",
    ]
    if target_exists:
        lines += [
            "",
            f"{target} already exists — merge the provider item below into its",
            "`providers.items` list (and set `providers.default` /",
            "`runtime_defaults.default_provider_name` to make it the default):",
        ]
    else:
        lines += [
            "",
            "Create the file with this minimal config, then `chmod 600` it:",
        ]
    lines += ["", "---"]
    if target_exists:
        item_yaml = yaml.safe_dump(
            {"providers": {"items": [build_provider_item(placeholder)]}},
            sort_keys=False,
            allow_unicode=True,
        )
        lines.append(item_yaml.rstrip())
    else:
        doc = minimal_config_payload(placeholder)
        lines.append(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).rstrip())
    lines += [
        "---",
        "",
        "Per-template suggested base_url / model:",
    ]
    for template in provider_templates():
        base = template.base_url or "<required>"
        model = template.model or "<required>"
        key_note = "  (keyless)" if template.keyless else ""
        lines.append(f"  {template.key:<18} {base:<52} {model}{key_note}")
    lines += [
        "",
        f"The canonical default chat-provider name is {DEFAULT_PROVIDER_NAME!r};",
        "`homemaster doctor --live` probes that provider.",
    ]
    return "\n".join(lines)


class _AuthPrompter(Protocol):
    """Input seam so tests can drive the wizard without a TTY."""

    def text(self, message: str, *, default: str = "") -> str: ...

    def secret(self, message: str) -> str: ...

    def ask_yes_no(self, message: str, *, default: bool = True) -> bool: ...

    def choose(self, message: str, options: list[str], *, default_index: int = 0) -> int: ...


class _ToolkitPrompter:
    """prompt_toolkit-backed prompts (hidden secret entry, defaults)."""

    def __init__(self, echo: Callable[[str], None]) -> None:
        from prompt_toolkit import PromptSession

        self._session: PromptSession[str] = PromptSession()
        self._echo = echo

    def text(self, message: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        value = self._session.prompt(f"{message}{suffix}: ")
        value = value.strip()
        return value or default

    def secret(self, message: str) -> str:
        return self._session.prompt(f"{message}: ", is_password=True).strip()

    def ask_yes_no(self, message: str, *, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            raw = self._session.prompt(f"{message} [{hint}]: ").strip().lower()
            if not raw:
                return default
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            self._echo("Please answer y or n.")

    def choose(self, message: str, options: list[str], *, default_index: int = 0) -> int:
        self._echo(message)
        for index, option in enumerate(options, start=1):
            marker = " (default)" if index - 1 == default_index else ""
            self._echo(f"  {index}) {option}{marker}")
        while True:
            raw = self._session.prompt(f"Select 1-{len(options)} [{default_index + 1}]: ").strip()
            if not raw:
                return default_index
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self._echo(f"Enter a number between 1 and {len(options)}.")


class _TyperPrompter:
    """Fallback when prompt_toolkit is unavailable on a live terminal."""

    def __init__(self, echo: Callable[[str], None]) -> None:
        self._echo = echo

    def text(self, message: str, *, default: str = "") -> str:
        value = typer.prompt(
            message,
            default=default or None,
            show_default=bool(default),
        )
        return str(value).strip()

    def secret(self, message: str) -> str:
        return str(typer.prompt(message, hide_input=True, default="")).strip()

    def ask_yes_no(self, message: str, *, default: bool = True) -> bool:
        return bool(_typer_confirm(message, default=default))

    def choose(self, message: str, options: list[str], *, default_index: int = 0) -> int:
        self._echo(message)
        for index, option in enumerate(options, start=1):
            marker = " (default)" if index - 1 == default_index else ""
            self._echo(f"  {index}) {option}{marker}")
        while True:
            raw = typer.prompt(f"Select 1-{len(options)}", default=str(default_index + 1))
            raw = str(raw).strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self._echo(f"Enter a number between 1 and {len(options)}.")


def _prompt_non_empty(
    prompter: _AuthPrompter,
    message: str,
    *,
    default: str = "",
) -> str:
    while True:
        value = prompter.text(message, default=default)
        if value:
            return value


def _prompt_positive_int(
    prompter: _AuthPrompter,
    message: str,
    *,
    default: int,
    echo: Callable[[str], None],
) -> int:
    while True:
        raw = prompter.text(message, default=str(default))
        try:
            value = int(raw)
        except ValueError:
            echo("Enter an integer.")
            continue
        if value <= 0:
            echo("Enter a positive integer.")
            continue
        return value


def _render_confirmation(answers: ProviderAnswers) -> str:
    return "\n".join(
        [
            "",
            "Review provider profile:",
            f"  name:                  {answers.name}",
            f"  api_format:            {answers.api_format}",
            f"  transport:             {answers.transport}",
            f"  auth_type:             {answers.auth_type}",
            f"  base_url:              {answers.base_url}",
            f"  model:                 {answers.model}",
            f"  api_key:               {mask_api_key(answers.api_key)}",
            f"  context_window_tokens: {answers.context_window_tokens}",
            f"  make default provider: {'yes' if answers.set_default else 'no'}",
            "",
        ]
    )


def _collect_answers(
    prompter: _AuthPrompter,
    echo: Callable[[str], None],
    *,
    existing_names: set[str],
) -> ProviderAnswers | None:
    """Run the question flow; returns None when the user bails at confirm."""

    templates = provider_templates()
    template_index = prompter.choose(
        "Select a provider template:",
        [t.title for t in templates],
        default_index=0,
    )
    template = templates[template_index]

    name = _prompt_non_empty(
        prompter,
        "Provider profile name",
        default=DEFAULT_PROVIDER_NAME,
    )
    base_url = _prompt_non_empty(
        prompter,
        "Base URL",
        default=template.base_url,
    )
    model = _prompt_non_empty(
        prompter,
        "Model",
        default=template.model,
    )

    auth_type = "api_key"
    if template.supports_auth_token:
        auth_index = prompter.choose(
            "Auth type (api_key = x-api-key header; auth_token = Authorization bearer):",
            ["api_key", "auth_token"],
            default_index=0,
        )
        auth_type = ["api_key", "auth_token"][auth_index]

    api_key = ""
    if template.keyless:
        echo(f"{template.title} runs keyless; no API key needed.")
    else:
        api_key = prompter.secret(
            f"API key for {name} (input hidden; leave empty to use "
            f"{env_override_hint(name)} later)"
        )
        if not api_key:
            echo(f"No API key stored; set {env_override_hint(name)} before running.")

    context_window = _prompt_positive_int(
        prompter,
        "Context window tokens",
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        echo=echo,
    )

    # Offer to make the new provider the default whenever nothing named
    # DEFAULT_PROVIDER_NAME is configured yet; otherwise keep the current
    # default unless the user explicitly opts in.
    has_default_candidate = bool(existing_names)
    set_default = prompter.ask_yes_no(
        f"Set {name!r} as the default provider?",
        default=not has_default_candidate,
    )

    answers = ProviderAnswers(
        name=name,
        api_format=template.api_format,
        transport=template.transport,
        auth_type=auth_type,
        base_url=base_url,
        model=model,
        api_key=api_key,
        context_window_tokens=context_window,
        set_default=set_default,
    )
    echo(_render_confirmation(answers))
    if not prompter.ask_yes_no("Write this provider to the config?", default=True):
        return None
    return answers


def run_auth_wizard(
    *,
    target_path: Path,
    prompter: _AuthPrompter,
    echo: Callable[[str], None] = typer.echo,
) -> int:
    """Drive the interactive flow end to end.  Returns a process exit code."""

    echo("HomeMaster auth — configure a chat provider")
    echo(f"target config: {target_path}")

    try:
        existing = _load_existing_payload(target_path)
    except ConfigError as exc:
        echo(f"error: {exc}")
        return 1

    answers = _collect_answers(
        prompter,
        echo,
        existing_names=_existing_provider_names(existing),
    )
    if answers is None:
        echo("Aborted; nothing was written.")
        return 1

    payload = (
        merge_providers_section(existing, answers)
        if existing is not None
        else minimal_config_payload(answers)
    )
    try:
        write_auth_config(target_path, payload)
    except OSError as exc:
        echo(f"error: cannot write {target_path}: {exc}")
        return 1

    # Black-box gate: the file we just wrote must load through the real
    # config path, not just our own serializer.
    from homemaster.config import load_config

    try:
        config = load_config(target_path)
        provider = config.get_provider(answers.name, kind="chat")
    except ConfigError as exc:
        echo(f"warning: wrote {target_path} but load_config rejects it: {exc}")
        return 1

    action = "updated" if existing is not None else "created"
    echo("")
    echo(f"Wrote {target_path} ({action}; permissions 600).")
    echo(
        f"Provider {provider.name!r}: api_format={provider.api_format} "
        f"model={provider.model} base_url={provider.base_url}"
    )
    if answers.name != DEFAULT_PROVIDER_NAME:
        echo(
            f"note: `homemaster doctor` checks the provider literally named "
            f"{DEFAULT_PROVIDER_NAME!r}; rename or add a {DEFAULT_PROVIDER_NAME!r} "
            "profile for the doctor checks."
        )
    echo("Next: run `homemaster doctor --live` to verify connectivity.")
    return 0


def auth_command(
    *,
    print_only: bool = False,
    config_path: str | Path | None = None,
    interactive: bool | None = None,
    prompter: _AuthPrompter | None = None,
    echo: Callable[[str], None] = typer.echo,
) -> int:
    """Entry point for the ``homemaster auth`` command.  Returns an exit code.

    ``app.py`` wires this as::

        raise typer.Exit(code=auth_command(print_only=..., config_path=...))

    ``interactive``/``prompter`` are test seams: when ``interactive`` is None
    the wizard runs only on a real TTY; ``prompter`` overrides the input
    backend entirely.
    """

    target = resolve_auth_config_path(config_path)
    if interactive is None:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())

    if print_only or not interactive:
        echo(render_print_only_output(target, target_exists=target.is_file()))
        return 0

    if prompter is None:
        try:
            import prompt_toolkit  # noqa: F401
        except ImportError:
            prompter = _TyperPrompter(echo)
        else:
            prompter = _ToolkitPrompter(echo)

    try:
        return run_auth_wizard(target_path=target, prompter=prompter, echo=echo)
    except (KeyboardInterrupt, EOFError):
        echo("\nAborted; nothing was written.")
        return 130


__all__ = [
    "ProviderAnswers",
    "ProviderTemplate",
    "auth_command",
    "build_provider_item",
    "env_override_hint",
    "mask_api_key",
    "merge_providers_section",
    "minimal_config_payload",
    "provider_templates",
    "render_print_only_output",
    "resolve_auth_config_path",
    "run_auth_wizard",
    "write_auth_config",
]
