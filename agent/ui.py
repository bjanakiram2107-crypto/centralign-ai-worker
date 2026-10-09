"""Terminal interface: a live trace of what the agent is doing, and the human prompts."""

import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()


def show_goal(goal: str, run_dir) -> None:
    console.print(Panel(goal, title="GOAL", border_style="cyan"))
    console.print(f"[dim]Evidence and state for this run: {run_dir}[/dim]\n")


def show_memory(text: str) -> None:
    console.print(Panel(text, title="COMPANY MEMORY (from earlier runs)", border_style="blue"))


def show_thinking(text: str) -> None:
    if text.strip():
        console.print(Panel(text.strip()[:1200], title="reasoning (summary)", border_style="grey50", style="grey62"))


def show_text(text: str) -> None:
    if text.strip():
        console.print(f"[bold]agent:[/bold] {text.strip()}")


def show_tool_call(step: int, name: str, tool_input: dict) -> None:
    args = json.dumps(tool_input, ensure_ascii=False)
    console.print(f"[bold yellow]{step:>2}. ACT[/bold yellow] {name}({args[:300]})")


def show_tool_result(ok: bool, result: str) -> None:
    style, label = ("green", "OBSERVE") if ok else ("red", "FAILED")
    first_lines = "\n".join(result.splitlines()[:12])
    console.print(Panel(first_lines, title=label, border_style=style, title_align="left"))


def show_recovery(note: str) -> None:
    console.print(f"[magenta]{note}[/magenta]")


def show_plan(plan: list) -> None:
    t = Table(title="PLAN", show_header=False, border_style="blue")
    for p in plan:
        mark = {"done": "[green]✓[/green]", "doing": "[yellow]▶[/yellow]", "skipped": "[dim]-[/dim]"}.get(
            p["status"], "○")
        t.add_row(mark, p["step"])
    console.print(t)


def show_final(state) -> None:
    color = {"completed": "green", "on_hold": "yellow"}.get(state.status, "red")
    body = state.summary
    if state.verification:
        body += "\n\nVerification: " + ("PASSED" if state.verification["passed"] else "FAILED")
    console.print(Panel(body, title=f"RESULT: {state.status.upper()}", border_style=color))
    console.print(f"[dim]Full report: {state.run_dir / 'report.md'}[/dim]")


class TerminalHuman:
    """The human approver / colleague, reached through the terminal."""

    def approve(self, summary: str) -> tuple[str, str]:
        console.print(Panel(summary, title="APPROVAL NEEDED (Finance Manager)", border_style="bold red"))
        choice = Prompt.ask("Approve this action?", choices=["approve", "reject"], default="approve")
        note = Prompt.ask("Optional note", default="")
        return ("approved" if choice == "approve" else "rejected"), note

    def ask(self, question: str) -> str:
        console.print(Panel(question, title="QUESTION FROM THE AGENT", border_style="bold cyan"))
        return Prompt.ask("Your answer")
