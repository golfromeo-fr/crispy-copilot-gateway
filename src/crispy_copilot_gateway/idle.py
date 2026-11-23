"""Idle auto-exit: graceful shutdown when no request arrives for a while.

Used for harness-spawned gateways (the ZCode wrapper): while the harness runs
it polls /healthz (chip tick), which keeps the gateway alive; when the harness
is closed and the user forgot to stop the gateway, the idle timer elapses and
uvicorn shuts down gracefully (in-flight requests finish first).
"""
from __future__ import annotations

import threading
import time


def arm_idle_exit(server, timeout_s: float, app, check_interval_s: float = 15.0) -> None:
    """Stamp last-request time via middleware; a daemon thread flips
    server.should_exit (uvicorn graceful shutdown) once idle for timeout_s.

    Kept deliberately simple: a thread + monotonic clock, no new deps.
    """
    state = {"last": time.monotonic()}

    @app.middleware("http")
    async def _idle_stamp(request, call_next):
        state["last"] = time.monotonic()
        return await call_next(request)

    def watch():
        while not server.should_exit:
            time.sleep(check_interval_s)
            if time.monotonic() - state["last"] >= timeout_s:
                server.should_exit = True  # graceful: in-flight requests finish
                return

    threading.Thread(target=watch, name="idle-exit", daemon=True).start()
    return state  # exposed so tests can stamp request activity directly
