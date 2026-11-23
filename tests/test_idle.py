"""Idle-exit watcher flips server.should_exit after the quiet window."""
import time

from fastapi import FastAPI

from crispy_copilot_gateway.idle import arm_idle_exit


class StubServer:
    def __init__(self):
        self.should_exit = False


def test_idle_exit_fires_after_quiet_window():
    server = StubServer()
    state = arm_idle_exit(server, timeout_s=0.2, app=FastAPI(), check_interval_s=0.05)
    time.sleep(0.5)
    assert server.should_exit is True


def test_idle_exit_holds_while_requests_arrive():
    server = StubServer()
    state = arm_idle_exit(server, timeout_s=0.3, app=FastAPI(), check_interval_s=0.05)
    for _ in range(8):  # keep stamping: never idle for 0.3s
        state["last"] = time.monotonic()
        time.sleep(0.05)
    assert server.should_exit is False
