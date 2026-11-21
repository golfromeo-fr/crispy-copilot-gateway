"""NiceGUI management UI: GitHub login, proxy keys, and Copilot usage, under /ui."""

from . import config, connections, keys, usage

_mounted = False


def mount(app) -> None:
    """Wire the UI into the FastAPI app; nicegui imported lazily. Configures once."""
    global _mounted
    from nicegui import core, ui

    if not _mounted:
        ui.page("/")(index)
        ui.run_with(app, mount_path="/ui", title="crispy-copilot-gateway",
                    storage_secret=config.storage_secret())
        _mounted = True
    else:
        app.mount("/ui", core.app)  # re-mount the configured app for extra apps (tests)
    # NOTE: socket.io connect gating is intentionally NOT done here - wrapping NiceGUI's
    # connect handler silently breaks event delivery (clicks never reach handlers). The
    # HTTP middleware gates the page, statics, and polling; a direct websocket upgrade
    # remains a known gap, acceptable for a 127.0.0.1-bound tool.


def _render_connections(container) -> None:
    from nicegui import ui
    from . import ui_common

    container.clear()
    with container:
        stored = connections.load_all()
        if not stored:
            ui.label("No GitHub account connected yet - use Connect via browser below.").classes("opacity-60")
        for label, entry in stored.items():
            endpoint = entry.get("api_endpoint") or "github.com"
            with ui.row().classes("items-center"):
                ui.label(f"{label}  added={entry.get('added_at', '')}  {endpoint}")
                ui.button("Disconnect", on_click=lambda lbl=label: ui_common.confirm(
                    "Disconnect account?",
                    f"Gateway requests for '{lbl}' will fail until you reconnect.",
                    "Disconnect", lambda lbl=lbl: _disconnect(container, lbl)))


def _disconnect(container, label: str) -> None:
    from nicegui import ui

    connections.remove(label)
    ui.notify(f"Disconnected '{label}'.", type="positive")
    _render_connections(container)


def _render_keys(container) -> None:
    from nicegui import ui
    from . import ui_common

    container.clear()
    with container:
        records = keys.list_keys()
        if not records:
            ui.label("No proxy keys yet - create one for each harness.").classes("opacity-60")
        for record in records:
            name = record["name"]
            with ui.row().classes("items-center"):
                ui.label(f"{name}  created={record['created_at']}  "
                         f"last_used={record['last_used'] or 'never'}")
                ui.button("Revoke", on_click=lambda n=name: ui_common.confirm(
                    "Revoke key?", f"Harnesses using '{n}' will stop working until you create a new key.",
                    "Revoke", lambda n=n: _revoke(container, n)))


def _revoke(container, name: str) -> None:
    from nicegui import ui

    keys.revoke(name)
    ui.notify(f"Revoked '{name}'.", type="positive")
    _render_keys(container)


def _render_usage(container) -> None:
    from nicegui import ui

    container.clear()
    with container:
        t = usage.totals()
        ui.label(f"requests={t['requests']}  credits={round(t['credits'], 4)}  "
                 f"cost_usd={round(t['cost_usd'], 6)}")
        rows = usage.recent(10)
        if not rows:
            ui.label("No requests yet.").classes("opacity-60")
        for row in rows:
            credits = round((row["nano_aiu"] or 0) / 1e9, 4)
            ui.label(f"{row['ts']}  {row['key_name']}  {row['model']}  credits={credits}")


def _key_dialog(raw: str) -> None:
    """One-time key disclosure: selectable monospace text with copy, never stored clear."""
    from nicegui import ui
    from . import ui_common

    with ui.dialog() as dialog, ui.card():
        ui.label("Copy your key now").classes("text-lg font-bold")
        ui.label("It is shown once and never stored in clear text.").classes("text-sm opacity-60")
        ui.label(raw).classes("font-mono").style("user-select: all")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Copy", on_click=lambda: ui_common.copy_text(raw)).props("flat dense")
            ui.button("Close", on_click=dialog.close)
    dialog.open()


def index() -> None:
    from nicegui import app, ui
    from . import ui_common, ui_models

    dark = ui.dark_mode(True)
    dark.bind_value(app.storage.user, "dark", backward=lambda v: True if v is None else v)
    with ui.row().classes("w-full max-w-3xl mx-auto items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("hub", size="sm")
            ui.label("crispy-copilot-gateway").classes("text-2xl font-bold")
            ui.label("Copilot gateway for any harness").classes("text-sm opacity-60")
        with ui.row().classes("items-center gap-1"):
            ui_common.dark_toggle(dark)
            ui.button("Reload page", on_click=lambda: ui.run_javascript("window.location.reload()")).props("flat dense")

    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        with ui.card().classes("w-full"):
            ui.label("GitHub connections").classes("text-lg font-bold")
            ui.label("Link a GitHub account; tokens never leave this machine.").classes("text-sm opacity-60")
            conns = ui.column()
            _render_connections(conns)
            state = {"flow": None, "label": "", "interval": 5.0}

            async def poll() -> None:
                if state["flow"] is None:
                    return
                try:
                    result = await connections.poll_once(state["flow"]["device_code"])
                except Exception:
                    return  # transient network error; keep polling
                if "access_token" in result:
                    state["flow"] = None; timer.active = False; code_area.clear()
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
            code_area = ui.column()

            async def connect() -> None:
                try:
                    flow = await connections.start_device_flow()
                except Exception as e:
                    ui.notify(f"Could not start device flow: {e}", type="negative")
                    return
                state.update(flow=flow, label=label_input.value,
                             interval=max(float(flow.get("interval", 5)), 5.0))
                timer.interval = state["interval"]
                code_area.clear()
                with code_area:
                    ui.label(f"Enter code: {flow['user_code']}").classes("text-lg font-mono")
                    ui.link("Open github.com/login/device", flow["verification_uri"]).props("target=_blank")
                    ui.label("Sign in, enter the code, authorize. The gateway detects it automatically.").classes(
                        "text-xs opacity-70")
                timer.active = True

            label_input = ui.input("Label", value="github")
            ui.button("Connect via browser", on_click=connect)

        with ui.card().classes("w-full"):
            ui.label("Proxy API keys").classes("text-lg font-bold")
            ui.label("One key per harness (ZCode, Claude Code, ...). Copy it when created - it is shown once.").classes(
                "text-sm opacity-60")
            key_rows = ui.column()
            _render_keys(key_rows)

            async def create_key() -> None:
                name = name_input.value.strip()
                try:
                    raw = keys.create(name)
                except ValueError:
                    ui.notify(f"Key name already exists: '{name}'", type="negative")
                    return
                name_input.value = ""
                _key_dialog(raw)
                _render_keys(key_rows)

            name_input = ui.input("Key name")
            ui.button("Create key", on_click=create_key)

        with ui.card().classes("w-full"):
            ui.label("Copilot usage").classes("text-lg font-bold")
            ui.label("Live credits burned through this gateway (1 credit = $0.01).").classes("text-sm opacity-60")
            usage_rows = ui.column()
            _render_usage(usage_rows)
            ui.button("Refresh", on_click=lambda: _render_usage(usage_rows))

        ui_models.card()
