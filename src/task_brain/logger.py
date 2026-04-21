"""Task Brain logger module with Claude Code style progress output."""

import logging
import sys
from typing import Any, Optional

from rich.console import Console
from rich.logging import RichHandler

# 配置rich console输出到stderr
console = Console(stderr=True)


class TaskBrainLogger:
    """Claude Code风格的任务执行日志"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"task_brain.{name}")
        self.logger.setLevel(logging.DEBUG)

        # 添加RichHandler
        if not self.logger.handlers:
            handler = RichHandler(
                console=console,
                show_time=True,
                show_path=False,
                markup=True,
                rich_tracebacks=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)

        self._current_task = None

    def start_task(self, description: str) -> None:
        """开始一个任务，显示spinner"""
        console.print(f"[blue]►[/blue] {description}...")
        self._current_task = description

    def complete_task(self, success: bool = True, message: Optional[str] = None) -> None:
        """完成当前任务"""
        if not self._current_task:
            return
        icon = "[green]✓[/green]" if success else "[red]✗[/red]"
        msg = message or self._current_task
        console.print(f"{icon} {msg}")
        self._current_task = None

    def info(self, message: str, **kwargs: Any) -> None:
        """输出信息级别日志"""
        extra_parts = []
        for k, v in kwargs.items():
            extra_parts.append(f"[dim]{k}=[/dim]{v}")
        extra = " ".join(extra_parts)
        if extra:
            console.print(f"  [dim]│[/dim] {message} {extra}")
        else:
            console.print(f"  [dim]│[/dim] {message}")

    def warning(self, message: str, **kwargs: Any) -> None:
        """输出警告"""
        extra_parts = []
        for k, v in kwargs.items():
            extra_parts.append(f"[yellow]{k}=[/yellow]{v}")
        extra = " ".join(extra_parts)
        if extra:
            console.print(f"  [yellow]⚠[/yellow] {message} {extra}")
        else:
            console.print(f"  [yellow]⚠[/yellow] {message}")

    def error(self, message: str, **kwargs: Any) -> None:
        """输出错误"""
        extra_parts = []
        for k, v in kwargs.items():
            extra_parts.append(f"[red]{k}=[/red]{v}")
        extra = " ".join(extra_parts)
        if extra:
            console.print(f"  [red]✗[/red] {message} {extra}")
        else:
            console.print(f"  [red]✗[/red] {message}")

    def debug(self, message: str, **kwargs: Any) -> None:
        """输出调试信息"""
        if not self.logger.isEnabledFor(logging.DEBUG):
            return
        extra_parts = []
        for k, v in kwargs.items():
            extra_parts.append(f"{k}={v}")
        extra = " ".join(extra_parts)
        if extra:
            console.print(f"  [dim]…[/dim] {message} {extra}", style="dim")
        else:
            console.print(f"  [dim]…[/dim] {message}", style="dim")


def get_logger(name: str) -> TaskBrainLogger:
    """获取模块logger"""
    return TaskBrainLogger(name)


# 配置根logger
def configure_logging(level: str = "INFO") -> None:
    """配置全局日志级别"""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                show_time=True,
                show_path=False,
                markup=True,
                rich_tracebacks=True,
            )
        ],
    )