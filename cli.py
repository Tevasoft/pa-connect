"""pdp_mock.cli — pdp-mock CLI"""
import click
import uvicorn

@click.group()
@click.version_option("0.1.0")
def cli():
    """🔌 pdp-mock — Local PDP mock for French e-invoicing 2026 testing"""

@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8042, show_default=True)
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on file changes")
@click.option("--log-level", default="info", type=click.Choice(["debug","info","warning","error"]))
def start(host, port, reload, log_level):
    """Start the mock PDP server."""
    click.echo(f"🚀 PDP mock starting at http://{host}:{port}")
    click.echo(f"   📖 API docs: http://localhost:{port}/docs")
    click.echo(f"   ❤️  Health:   http://localhost:{port}/health")
    uvicorn.run("pdp_mock.app:app", host=host, port=port, reload=reload, log_level=log_level)

@cli.command()
@click.option("--url", default="http://localhost:8042", show_default=True)
def status(url):
    """Check the health of a running mock PDP."""
    from pdp_mock.client import PDPClient, PDPError
    try:
        with PDPClient(url) as c:
            h = c.health()
        click.echo(f"✅ PDP mock is running — {h['invoices']} invoice(s), uptime {h['uptime_seconds']:.0f}s")
    except Exception as e:
        click.echo(f"❌ Could not reach mock PDP at {url}: {e}", err=True)
        raise SystemExit(1)

@cli.command()
@click.option("--url", default="http://localhost:8042", show_default=True)
def reset(url):
    """Reset all mock state (invoices, webhooks, scenarios)."""
    from pdp_mock.client import PDPClient
    with PDPClient(url) as c:
        c.reset()
    click.echo("✅ Mock state reset")

if __name__ == "__main__":
    cli()
