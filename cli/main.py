"""
WHOOP CLI — thin layer over WhoopService using shared core.
All async calls are wrapped with asyncio.run().
"""

import asyncio
import json
import sys
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from whoop.services import WhoopService, _build_service_from_env

load_dotenv()

app = typer.Typer(
    name="whoop",
    help="WHOOP health data CLI",
    no_args_is_help=True,
)
console = Console()


def _get_service() -> WhoopService:
    try:
        return _build_service_from_env()
    except (KeyError, EnvironmentError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(1)


def _run(coro):
    return asyncio.run(coro)


async def _run_with_cleanup(service: WhoopService, coro):
    try:
        return await coro
    finally:
        await service.aclose()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def summary(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="YYYY-MM-DD (default: today)"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON"),
):
    """Show daily health summary with recommendation."""
    service = _get_service()
    result = _run(_run_with_cleanup(service, service.get_daily_summary(date=date)))

    if raw:
        console.print_json(json.dumps(result.to_dict()))
        return

    console.print(f"\n[bold]{result.format_line()}[/bold]\n")


@app.command()
def recovery(
    start: Optional[str] = typer.Option(None, "--start", help="Start datetime ISO"),
    end: Optional[str] = typer.Option(None, "--end", help="End datetime ISO"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON"),
):
    """Show recovery metrics (score, HRV, resting HR, SpO2)."""
    from whoop.schema.mappers import map_recovery

    service = _get_service()
    result = _run(_run_with_cleanup(service, service.get_recovery(start=start, end=end)))

    if result is None:
        console.print("[yellow]No recovery data found.[/yellow]")
        return

    mapped = map_recovery(result)

    if raw:
        console.print_json(json.dumps({
            "score": mapped.score,
            "hrv_rmssd": mapped.hrv_rmssd,
            "resting_hr": mapped.resting_hr,
            "spo2": mapped.spo2,
        }))
        return

    table = Table(title="Recovery", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Recovery Score", f"{mapped.score}%" if mapped.score is not None else "N/A")
    table.add_row("HRV (RMSSD)", f"{mapped.hrv_rmssd:.1f} ms" if mapped.hrv_rmssd else "N/A")
    table.add_row("Resting HR", f"{mapped.resting_hr} bpm" if mapped.resting_hr else "N/A")
    table.add_row("SpO2", f"{mapped.spo2:.1f}%" if mapped.spo2 else "N/A")
    console.print(table)


@app.command()
def sleep(
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    raw: bool = typer.Option(False, "--raw"),
):
    """Show sleep data (score, duration, efficiency)."""
    from whoop.schema.mappers import map_sleep

    service = _get_service()
    result = _run(_run_with_cleanup(service, service.get_sleep(start=start, end=end)))

    if result is None:
        console.print("[yellow]No sleep data found.[/yellow]")
        return

    mapped = map_sleep(result)

    if raw:
        console.print_json(json.dumps({
            "score": mapped.score,
            "duration_hours": mapped.duration_hours,
            "efficiency": mapped.efficiency,
        }))
        return

    table = Table(title="Sleep", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Sleep Score", f"{mapped.score}" if mapped.score is not None else "N/A")
    table.add_row(
        "Duration",
        f"{mapped.duration_hours:.1f} hrs" if mapped.duration_hours else "N/A",
    )
    table.add_row(
        "Efficiency",
        f"{mapped.efficiency * 100:.0f}%" if mapped.efficiency else "N/A",
    )
    console.print(table)


@app.command()
def trends(
    days: int = typer.Option(7, "--days", "-n", help="Number of days (1-90)"),
    raw: bool = typer.Option(False, "--raw"),
):
    """Show health metric trends over N days."""
    service = _get_service()

    try:
        report = _run(_run_with_cleanup(service, service.get_trends(days=days)))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if raw:
        console.print_json(json.dumps(report.to_dict()))
        return

    table = Table(title=f"Trends ({report.from_date} → {report.to_date})")
    table.add_column("Metric", style="cyan")
    table.add_column("Avg", justify="right")
    table.add_column("Direction", justify="center", style="bold")
    table.add_column("Change", justify="right")

    for m in report.metrics:
        avg_str = f"{m.average:.1f}" if m.average is not None else "N/A"
        change_str = f"{m.change_pct:+.1f}%" if m.change_pct is not None else "N/A"
        color = "green" if m.direction == "↑" else ("red" if m.direction == "↓" else "white")
        table.add_row(
            m.metric,
            avg_str,
            f"[{color}]{m.direction}[/{color}]",
            change_str,
        )

    console.print(table)


@app.command()
def auth(
    action: str = typer.Argument("status", help="status | login | logout"),
):
    """Manage WHOOP OAuth token (status / login / logout)."""
    service = _get_service()

    if action == "status":
        status = service.auth_status()
        if status["authenticated"]:
            expired = status.get("expired")
            color = "red" if expired else "green"
            label = "EXPIRED" if expired else "valid"
            console.print(
                f"[{color}]Authenticated[/{color}] — token is {label}, "
                f"expires at {status['expires_at']}"
            )
        else:
            console.print("[red]Not authenticated.[/red] Run: whoop auth login")

    elif action == "login":
        console.print("Starting OAuth flow...")
        token = service.login()
        console.print("[green]Authentication successful.[/green]")

    elif action == "logout":
        service.logout()
        console.print("[yellow]Logged out. Token cleared.[/yellow]")

    else:
        console.print(f"[red]Unknown action:[/red] {action}. Use: status, login, logout")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
