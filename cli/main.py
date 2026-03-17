"""
WHOOP CLI — thin layer over WhoopService using shared core.
All async calls are wrapped with asyncio.run().
"""

import asyncio
import json
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
    table.add_row("HRV (RMSSD)", f"{mapped.hrv_rmssd:.1f} ms" if mapped.hrv_rmssd is not None else "N/A")
    table.add_row("Resting HR", f"{mapped.resting_hr} bpm" if mapped.resting_hr is not None else "N/A")
    table.add_row("SpO2", f"{mapped.spo2:.1f}%" if mapped.spo2 is not None else "N/A")
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
            "respiratory_rate": mapped.respiratory_rate,
            "sleep_consistency": mapped.sleep_consistency,
            "sleep_needed": mapped.sleep_needed,
        }))
        return

    table = Table(title="Sleep", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Sleep Score", f"{mapped.score}" if mapped.score is not None else "N/A")
    table.add_row(
        "Duration",
        f"{mapped.duration_hours:.1f} hrs" if mapped.duration_hours is not None else "N/A",
    )
    table.add_row(
        "Efficiency",
        f"{mapped.efficiency * 100:.0f}%" if mapped.efficiency is not None else "N/A",
    )
    if mapped.respiratory_rate is not None:
        table.add_row("Respiratory Rate", f"{mapped.respiratory_rate:.1f} breaths/min")
    if mapped.sleep_consistency is not None:
        table.add_row("Consistency", f"{mapped.sleep_consistency}%")
    if mapped.sleep_needed:
        baseline_hrs = mapped.sleep_needed.get("baseline_milli")
        if baseline_hrs is not None:
            table.add_row("Sleep Needed (baseline)", f"{baseline_hrs / 3_600_000:.1f} hrs")
    console.print(table)


@app.command()
def body(
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON"),
):
    """Show body measurements (height, weight, max HR)."""
    from whoop.schema.mappers import map_body_measurement

    service = _get_service()
    result = _run(_run_with_cleanup(service, service.get_body_measurement()))

    mapped = map_body_measurement(result)

    if raw:
        console.print_json(json.dumps(mapped.to_dict()))
        return

    table = Table(title="Body Measurements", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row(
        "Height",
        f"{mapped.height_meter:.2f} m ({mapped.height_meter * 100:.0f} cm)"
        if mapped.height_meter else "N/A",
    )
    table.add_row(
        "Weight",
        f"{mapped.weight_kilogram:.1f} kg"
        if mapped.weight_kilogram else "N/A",
    )
    table.add_row(
        "Max Heart Rate",
        f"{mapped.max_heart_rate} bpm"
        if mapped.max_heart_rate else "N/A",
    )
    # BMI — computed if both height and weight are available
    if mapped.height_meter and mapped.weight_kilogram:
        bmi = mapped.weight_kilogram / (mapped.height_meter ** 2)
        table.add_row("BMI", f"{bmi:.1f}")

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
    action: str = typer.Argument("status", help="status | login | login-headless | logout"),
):
    """Manage WHOOP OAuth token (status / login / login-headless / logout)."""
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
        console.print("Starting OAuth flow (browser)...")
        token = service.login()
        console.print("[green]Authentication successful.[/green]")

    elif action == "login-headless":
        console.print("Starting headless OAuth flow (for VPS / bot)...")
        token = service.login_headless()
        console.print("[green]Authentication successful.[/green]")

    elif action == "logout":
        service.logout()
        console.print("[yellow]Logged out. Token cleared.[/yellow]")

    else:
        console.print(f"[red]Unknown action:[/red] {action}. Use: status, login, login-headless, logout")
        raise typer.Exit(1)


@app.command()
def export(
    days: int = typer.Option(7, "--days", "-n", help="Number of days to export (1-90)"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path (default: stdout)"),
):
    """Export all health data for N days as JSON."""
    service = _get_service()

    try:
        export_data = _run(_run_with_cleanup(service, service.get_export(days=days)))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if output:
        import pathlib
        pathlib.Path(output).write_text(json.dumps(export_data, indent=2, default=str))
        console.print(f"[green]Exported {days} days to {output}[/green]")
    else:
        console.print_json(json.dumps(export_data, indent=2, default=str))


@app.command()
def raw(
    endpoint: str = typer.Argument(..., help="profile | body | recovery | sleep | workouts | cycles"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
):
    """Dump raw WHOOP API response for debugging."""
    service = _get_service()

    async def _fetch():
        try:
            if endpoint == "profile":
                return await service.get_profile()
            elif endpoint == "body":
                return await service.get_body_measurement()
            elif endpoint == "recovery":
                return await service.get_recovery(start=start, end=end)
            elif endpoint == "sleep":
                return await service.get_sleep(start=start, end=end)
            elif endpoint == "workouts":
                return await service.get_workouts(start=start, end=end)
            elif endpoint == "cycles":
                return await service.get_cycles(start=start, end=end)
            else:
                console.print(f"[red]Unknown endpoint:[/red] {endpoint}")
                console.print("Available: profile, body, recovery, sleep, workouts, cycles")
                raise typer.Exit(1)
        finally:
            await service.aclose()

    result = _run(_fetch())
    console.print_json(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    app()
