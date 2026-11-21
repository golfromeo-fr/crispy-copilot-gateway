"""Copilot models card: upstream model IDs, copyable for harness configs (ZCode etc.)."""
from . import ui_common


def card() -> None:
    from nicegui import ui

    state: dict = {"ids": [], "models": [], "error": None, "filter": "", "count_label": None}
    with ui_common.panel("Copilot models", "model ids for harness configs", "memory"):
        ui.label("Model ids Copilot serves for this account - copy the ones your harness needs.").classes(
            "text-sm opacity-60")
        count_label = ui.label("").classes("text-xs opacity-50")
        with ui.scroll_area().classes("max-h-96 w-full"):
            rows = ui.column()
        filt = ui.input(placeholder="Filter models...").classes("w-full")

        def refilter(change) -> None:
            state["filter"] = change.value
            _render(rows, state)

        filt.on_value_change(refilter)
        with ui.row():
            ui.button("Copy all ids", on_click=lambda: _copy_all(state["ids"]))
            ui.button("Refresh", on_click=lambda: load(rows, state))
        ui.timer(0.1, lambda: load(rows, state), once=True)
    state["count_label"] = count_label


def _copy(text: str) -> None:
    from . import ui_common

    ui_common.copy_text(text)


def _copy_all(ids: list[str]) -> None:
    from nicegui import ui

    if not ids:
        return
    text = "\n".join(ids)
    ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
    ui.notify(f"Copied {len(ids)} model ids", type="positive")


def _visible(state) -> list[dict]:
    """Enabled first, then by id, filtered case-insensitively on id or name."""
    models = sorted(state.get("models") or [],
                    key=lambda m: ((m.get("policy") or {}).get("state") != "enabled",
                                   m.get("id") or ""))
    needle = (state.get("filter") or "").lower()
    if not needle:
        return models
    return [m for m in models
            if needle in (m.get("id") or "").lower()
            or needle in (m.get("model_name") or m.get("display_name") or "").lower()]


def _render(rows, state, models: list[dict] | None = None, error: str | None = None) -> None:
    from nicegui import ui

    if models is not None:
        state["models"], state["error"] = models, error
    error = state.get("error")
    all_models = state.get("models") or []
    shown = _visible(state)
    count = (f"{len(shown)} of {len(all_models)} ids" if not error
             else f"{len(shown)} ids")  # no total when the fetch failed
    if state.get("count_label") is not None:
        state["count_label"].text = count
    rows.clear()
    ids: list[str] = []
    with rows:
        if error:
            ui.label(error).classes("text-red")
        elif not all_models:
            ui.label("no models returned").classes("opacity-60")
        for m in shown:  # stale rows keep working (and filtering) after a refresh error
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
        if shown:
            ui.label("one id per line - paste into your harness provider config").classes("opacity-60")
    state["ids"] = ids


async def load(rows, state) -> None:
    from . import proxy  # circular-import safety: proxy imports ui lazily in build_app

    try:
        models = await proxy.fetch_models()
    except Exception as e:
        _render(rows, state, models=None, error=str(e))
    else:
        _render(rows, state, models=models)
