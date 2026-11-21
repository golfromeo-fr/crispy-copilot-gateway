"""NiceGUI management UI: GitHub login, proxy keys, and Copilot usage, under /ui."""
import webbrowser

from . import connections, keys, usage

_mounted = False


def mount(app) -> None:
    """Wire the UI into the FastAPI app; nicegui imported lazily. Configures once."""
    global _mounted
    from nicegui import core, ui

    if not _mounted:
        from .proxy import _gate_socketio

        ui.page("/")(index)
        ui.run_with(app, mount_path="/ui", title="crispy-copilot-gateway")
        _gate_socketio(core.sio)
        _mounted = True
    else:
        app.mount("/ui", core.app)  # re-mount the configured app for extra apps (tests)


def _render_connections(container) -> None:
    from nicegui import ui

    container.clear()
    with container:
        for label, entry in connections.load_all().items():
            with ui.row().classes("items-center"):
                endpoint = entry.get("api_endpoint") or "github.com"
                ui.label(f"{label}  added={entry.get('added_at', '')}  {endpoint}")
                ui.button("Disconnect", on_click=lambda lbl=label: _disconnect(container, lbl))


def _disconnect(container, label: str) -> None:
    connections.remove(label)
    _render_connections(container)


def _render_keys(container) -> None:
    from nicegui import ui

    container.clear()
    with container:
        for record in keys.list_keys():
            with ui.row().classes("items-center"):
                ui.label(f"{record['name']}  created={record['created_at']}  "
                         f"last_used={record['last_used'] or 'never'}")
                ui.button("Revoke", on_click=lambda name=record["name"]: _revoke(container, name))


def _revoke(container, name: str) -> None:
    keys.revoke(name)
    _render_keys(container)


def _render_usage(container) -> None:
    from nicegui import ui

    container.clear()
    with container:
        t = usage.totals()
        ui.label(f"requests={t['requests']}  credits={round(t['credits'], 2)}  "
                 f"cost_usd={round(t['cost_usd'], 4)}")
        for row in usage.recent(10):
            credits = round((row["nano_aiu"] or 0) / 1e9, 2)
            ui.label(f"{row['ts']}  {row['key_name']}  {row['model']}  credits={credits}")


def index() -> None:
    from nicegui import ui

    with ui.card():
        ui.label("GitHub connections").classes("text-lg font-bold")
        conns = ui.column()
        _render_connections(conns)
        state = {"flow": None, "label": "", "interval": 5.0}

        async def poll() -> None:
            if state["flow"] is None:
                return
            result = await connections.poll_once(state["flow"]["device_code"])
            if "access_token" in result:
                state["flow"] = None; timer.active = False; code_label.text = ""
                try:
                    await connections.complete_login(state["label"], result["access_token"])
                except ValueError:
                    ui.notify("GitHub account has no Copilot access - not connected.", type="negative")
                    return
                ui.notify(f"Connected '{state['label']}'.", type="positive")
                _render_connections(conns)
            elif result.get("error") == "slow_down":
                state["interval"] += 5
                timer.interval = state["interval"]
            elif result.get("error") != "authorization_pending":
                ui.notify(f"Device flow failed: {result.get('error')}", type="negative")
                state["flow"] = None; timer.active = False

        timer = ui.timer(1.0, poll, active=False)
        code_label = ui.label("")

        async def connect() -> None:
            flow = await connections.start_device_flow()
            state.update(flow=flow, label=label_input.value,
                         interval=max(float(flow.get("interval", 5)), 5.0))
            timer.interval = state["interval"]
            code_label.text = f"Enter code: {flow['user_code']}"
            webbrowser.open(flow["verification_uri"])
            timer.active = True

        label_input = ui.input("Label", value="github")
        ui.button("Connect via browser", on_click=connect)

    with ui.card():
        ui.label("Proxy API keys").classes("text-lg font-bold")
        key_rows = ui.column()
        _render_keys(key_rows)
        key_display = ui.label("").classes("font-mono")

        async def create_key() -> None:
            name = name_input.value.strip()
            try:
                raw = keys.create(name)
            except ValueError:
                ui.notify(f"Key name already exists: '{name}'", type="negative")
                return
            name_input.value = ""
            key_display.text = f"New key (copy it now - shown once): {raw}"
            _render_keys(key_rows)

        name_input = ui.input("Key name")
        ui.button("Create key", on_click=create_key)

    with ui.card():
        ui.label("Copilot usage").classes("text-lg font-bold")
        usage_rows = ui.column()
        _render_usage(usage_rows)
        ui.button("Refresh", on_click=lambda: _render_usage(usage_rows))
