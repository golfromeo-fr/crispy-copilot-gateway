"""crispy-copilot CLI: serve the gateway, log in to GitHub, manage proxy keys."""
import asyncio, time, webbrowser
import typer

from . import config, connections, keys

app = typer.Typer(no_args_is_help=True)

@app.command()
def serve(host: str = typer.Option(config.DEFAULT_HOST, "--host"),
          port: int = typer.Option(config.DEFAULT_PORT, "--port")) -> None:
    import uvicorn
    from .proxy import build_app
    uvicorn.run(build_app(), host=host, port=port)

@app.command()
def login(label: str) -> None:
    """Connect a GitHub account via the OAuth device flow."""
    if not connections.CLIENT_ID:
        typer.echo("No OAuth client ID. Register an OAuth app at https://github.com/settings/"
                   "developers (device flow needs no secret), then export CRISPY_COPILOT_CLIENT_ID.")
        raise typer.Exit(1)
    asyncio.run(_device_flow(label))

async def _device_flow(label: str) -> None:
    flow = await connections.start_device_flow()
    interval = max(int(flow.get("interval", 5)), 5)
    typer.echo(f"One-time code: {flow['user_code']}")
    webbrowser.open(flow["verification_uri"])
    while True:
        result = await connections.poll_once(flow["device_code"])
        if "access_token" in result:
            connections.save(label, result["access_token"])
            typer.echo(f"Connected '{label}'.")
            return
        error = result.get("error", "")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            typer.echo(f"Device flow failed: {error}")
            raise typer.Exit(1)
        time.sleep(interval + 1)

@app.command()
def key(name: str) -> None:
    typer.echo(keys.create(name))

@app.command(name="keys")
def keys_list() -> None:
    for record in keys.list_keys():
        typer.echo(f"{record['name']:<24} last_used={record['last_used'] or 'never'}")

@app.command()
def revoke(name: str) -> None:
    typer.echo("revoked" if keys.revoke(name) else "not found")

def main() -> None: app()

if __name__ == "__main__":
    main()
