"""Shared management-UI building blocks: clipboard, confirm dialogs, dark toggle."""
from __future__ import annotations


def copy_text(text: str) -> None:
    """Copy text to the browser clipboard and notify."""
    from nicegui import ui

    ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
    ui.notify(f"Copied: {text}", type="positive")


def confirm(title: str, message: str, confirm_label: str, on_yes) -> None:
    """Open a Cancel/confirm dialog; Escape or click-outside cancels (not persistent)."""
    from nicegui import ui

    with ui.dialog() as dialog, ui.card():
        ui.label(title).classes("text-lg font-bold")
        ui.label(message).classes("opacity-80")
        with ui.row().classes("w-full justify-end gap-2"):

            def _yes() -> None:
                dialog.close()
                on_yes()

            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(confirm_label, icon="warning", color="negative", on_click=_yes)
    dialog.open()


def dark_toggle(dark) -> None:
    """Flat button toggling dark mode; notifies with the new mode."""
    from nicegui import ui

    def toggle() -> None:
        dark.toggle()
        ui.notify(f"Dark mode {'on' if dark.value else 'off'}", type="info")

    ui.button(icon="dark_mode", on_click=toggle).props("flat dense")
