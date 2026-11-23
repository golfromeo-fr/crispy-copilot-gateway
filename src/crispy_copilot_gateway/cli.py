"""crispy-copilot CLI: serve the gateway, log in to GitHub, manage proxy keys."""
import asyncio

import typer

from . import config, connections, keys

app = typer.Typer(no_args_is_help=True)

@app.command()
def serve(host: str = typer.Option(config.DEFAULT_HOST, "--host"),
          port: int = typer.Option(config.DEFAULT_PORT, "--port"),
          idle_exit: float = typer.Option(
              0.0, "--idle-exit",
              help="minutes without any request before a graceful shutdown (0 = never)")) -> None:
    import uvicorn
    from .proxy import build_app
    app = build_app()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
    if idle_exit > 0:
        from .idle import arm_idle_exit
        arm_idle_exit(server, idle_exit * 60.0, app)
    server.run()

@app.command()
def login(label: str) -> None:
    """Connect a GitHub account via the OAuth device flow."""
    asyncio.run(_device_flow(label))

async def _device_flow(label: str) -> None:
    flow = await connections.start_device_flow()
    interval = max(int(flow.get("interval", 5)), 5)
    typer.echo(f"One-time code: {flow['user_code']}")
    typer.echo(f"Open {flow['verification_uri']} and enter the code to authorize.")
    while True:
        result = await connections.poll_once(flow["device_code"])
        if "access_token" in result:
            try:
                await connections.complete_login(label, result["access_token"])
            except ValueError as e:
                typer.echo(str(e))
                raise typer.Exit(1)
            typer.echo(f"Connected '{label}'.")
            return
        error = result.get("error", "")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            typer.echo(f"Device flow failed: {error}")
            raise typer.Exit(1)
        await asyncio.sleep(interval + 1)

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
