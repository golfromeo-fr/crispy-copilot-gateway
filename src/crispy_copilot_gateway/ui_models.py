"""Copilot models card: upstream model IDs, copyable for harness configs (ZCode etc.)."""


def card() -> None:
    from nicegui import ui

    state: dict = {"ids": []}
    with ui.card():
        ui.label("Copilot models").classes("text-lg font-bold")
        rows = ui.column()
        with ui.row():
            ui.button("Copy all ids", on_click=lambda: _copy_all(state["ids"]))
            ui.button("Refresh", on_click=lambda: load(rows, state))
        ui.timer(0.1, lambda: load(rows, state), once=True)


def _copy(text: str) -> None:
    from nicegui import ui

    ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
    ui.notify(f"Copied: {text}", type="positive")


def _copy_all(ids: list[str]) -> None:
    from nicegui import ui

    if not ids:
        return
    text = "\n".join(ids)
    ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
    ui.notify(f"Copied {len(ids)} model ids", type="positive")


def _render(rows, state, models: list[dict], error: str | None = None) -> None:
    from nicegui import ui

    rows.clear()
    ids: list[str] = []
    with rows:
        if error:
            ui.label(error).classes("text-red")
        elif not models:
            ui.label("no models returned").classes("opacity-60")
        else:
            for m in models:
                mid = m.get("id", "")
                ids.append(mid)
                with ui.row().classes("items-center"):
                    ui.label(mid).classes("font-mono")
                    name = m.get("model_name") or m.get("display_name")
                    if name and name != mid:
                        ui.label(name).classes("text-sm opacity-60")
                    if ((m.get("policy") or {}).get("state") or "enabled") != "enabled":
                        ui.badge("disabled", color="grey")
                    eps = m.get("supported_endpoints") or []
                    ui.label(", ".join(eps) if eps else "chat (default)").classes("text-xs opacity-50")
                    ui.button(icon="content_copy", on_click=lambda mid=mid: _copy(mid)).props("flat dense")
            ui.label("one id per line - paste into your harness provider config").classes("opacity-60")
    state["ids"] = ids


async def load(rows, state) -> None:
    from . import proxy  # circular-import safety: proxy imports ui lazily in build_app

    try:
        models = await proxy.fetch_models()
    except Exception as e:
        _render(rows, state, [], error=str(e))
    else:
        _render(rows, state, models)
