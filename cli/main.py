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

from whoop.errors import ErrorCode, WhoopError
from whoop.services import WhoopService, _build_service_from_env

load_dotenv()

app = typer.Typer(
    name="whoop",
    help="WHOOP health data CLI",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

# Exit codes let wrappers/agents tell "owner must act" from "retry later".
_EXIT_CODES = {
    ErrorCode.AUTH_REQUIRED: 3,
    ErrorCode.REAUTH_REQUIRED: 3,
    ErrorCode.INSUFFICIENT_SCOPE: 3,
    ErrorCode.TRANSIENT_ERROR: 4,
    ErrorCode.RATE_LIMITED: 4,
    ErrorCode.TOKEN_STORE_ERROR: 5,
    ErrorCode.CONFIG_ERROR: 5,
}


def _get_service() -> WhoopService:
    try:
        return _build_service_from_env()
    except (KeyError, EnvironmentError, ValueError) as exc:
        err_console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(5)


def _fail(exc: WhoopError) -> typer.Exit:
    err_console.print(f"[red]{exc.code.value}:[/red] {exc.message}")
    err_console.print(exc.hint)
    if exc.retry_after is not None:
        err_console.print(f"Retry after {exc.retry_after:.0f}s")
    return typer.Exit(_EXIT_CODES.get(exc.code, 1))


def _run(coro):
    try:
        return asyncio.run(coro)
    except WhoopError as exc:
        raise _fail(exc)


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
    action: str = typer.Argument("status", help="status | check | login | login-headless | logout"),
):
    """Manage WHOOP OAuth (status / check / login / login-headless / logout).

    status — reads the token file only (no network, no refresh);
    check  — real API call: proves the token works, refreshes if needed;
    login, login-headless — explicit interactive login.
    """
    service = _get_service()

    if action == "status":
        status = service.auth_status()
        _print_auth_status(status)
        if not status["authenticated"]:
            raise typer.Exit(5 if status["state"] == "store_error" else 3)

    elif action == "check":
        result = _run(_run_with_cleanup(service, service.auth_check()))
        _print_auth_status(result)
        check = result["check"]
        if check["ok"]:
            console.print("[green]Check OK:[/green] WHOOP API accepted the token.")
        else:
            code = ErrorCode(check["code"])
            err_console.print(f"[red]Check failed — {code.value}:[/red] {check['message']}")
            err_console.print(check["hint"])
            raise typer.Exit(_EXIT_CODES.get(code, 1))

    elif action in ("login", "login-headless"):
        try:
            if action == "login":
                console.print("Starting OAuth flow (browser)...")
                service.login()
            else:
                console.print("Starting headless OAuth flow (for VPS)...")
                service.login_headless()
        except WhoopError as exc:
            raise _fail(exc)
        except (RuntimeError, OSError, EOFError) as exc:  # TimeoutError is an OSError
            err_console.print(f"[red]Login failed:[/red] {exc or type(exc).__name__}")
            raise typer.Exit(1)
        console.print("[green]Authentication successful.[/green] Verify with: whoop auth check")

    elif action == "logout":
        try:
            service.logout()
        except WhoopError as exc:
            raise _fail(exc)
        console.print("[yellow]Logged out. Token cleared.[/yellow]")

    else:
        err_console.print(
            f"[red]Unknown action:[/red] {action}. Use: status, check, login, login-headless, logout"
        )
        raise typer.Exit(1)


def _print_auth_status(status: dict) -> None:
    state = status["state"]
    color = {"valid": "green", "refresh_needed": "yellow"}.get(state, "red")
    console.print(f"State: [{color}]{state}[/{color}]")
    if status.get("expires_at"):
        console.print(f"Access token expires at: {status['expires_at']}")
    if "has_refresh_token" in status:
        console.print(f"Refresh token stored: {'yes' if status['has_refresh_token'] else 'no'}")
    if status.get("missing_scopes"):
        console.print(f"[red]Missing scopes:[/red] {' '.join(status['missing_scopes'])}")
    if state == "auth_required":
        console.print("Not logged in. Run: whoop auth login-headless")
    elif state == "reauth_required":
        console.print("Re-login required. Run: whoop auth login-headless")
    elif state == "store_error":
        console.print(f"[red]{status['error']['message']}[/red]")
        console.print(status["error"]["hint"])


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
