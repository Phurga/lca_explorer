# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app started on your own computer stops with its browser tabs.

Each tab of the page says it is open (``/api/tab/alive``, every 20 s) and that it closes
(``/api/tab/bye``, as it goes). Once no tab is left for :attr:`Lifetime.grace` seconds (a
reload is back before), or when the page's Shut down button asks, the server stops, after
the jobs under way (an import, a build) finish. A tab not heard of for
:attr:`Lifetime.silence` seconds counts as closed (a browser that crashed); browsers slow the
timers of hidden tabs to once a minute, hence the margin. When no tab opens the app within
:attr:`Lifetime.first` seconds (the browser did not open it), the server stops too: started by
lca_explorer, it runs without a window, and nothing else would stop it.

Not used for a server reached through SSH: a tunnel that drops would stop it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class Lifetime:
    def __init__(self, stop: Callable[[], None], busy: Callable[[], list[str]] = lambda: [],
                 grace: float = 10.0, silence: float = 150.0, first: float = 120.0, tick: float = 1.0):
        self.stop, self.busy = stop, busy
        self.grace, self.silence, self.first, self.tick = grace, silence, first, tick
        self.started = time.monotonic()
        self.tabs: dict[str, float] = {}  # tab -> when it was last heard of (time.monotonic)
        self.seen = False  # a tab opened once: until then (the browser starting) nothing stops
        self.asked = False  # the page asked to shut down
        self.stopped = False
        self._lock = threading.Lock()

    def alive(self, tab: str) -> None:
        with self._lock:
            self.tabs[tab] = time.monotonic()
            self.seen = True

    def bye(self, tab: str) -> None:
        with self._lock:
            self.tabs.pop(tab, None)

    def shut_down(self) -> list[str]:
        """Stop as soon as no job runs; the titles of those it waits for."""
        self.asked = True
        return self.busy()

    def check(self, now: float, empty_since: float | None) -> float | None:
        """One look: stops the server when it is time; when the tabs were all gone (None: some open)."""
        with self._lock:
            for tab, heard in list(self.tabs.items()):
                if now - heard > self.silence:
                    del self.tabs[tab]
            empty = self.seen and not self.tabs
        if empty and empty_since is None:
            empty_since = now
        elif not empty:
            empty_since = None
        never_opened = not self.seen and now - self.started >= self.first
        due = self.asked or never_opened or (empty_since is not None and now - empty_since >= self.grace)
        if due and not self.busy():
            self.stopped = True
            self.stop()
        return empty_since

    def watch(self) -> threading.Thread:
        def run():
            empty_since, last = None, time.monotonic()
            while not self.stopped:
                time.sleep(self.tick)
                now = time.monotonic()
                if now - last > 10 * self.tick + 5:
                    # The computer slept: the tabs had no chance to speak, they count from now
                    with self._lock:
                        self.tabs = dict.fromkeys(self.tabs, now)
                    empty_since = None
                    self.started += now - last  # the time asleep does not count as waiting for a tab
                last = now
                empty_since = self.check(now, empty_since)

        thread = threading.Thread(target=run, daemon=True, name="lifetime")
        thread.start()
        return thread


def add_routes(app, lifetime: Lifetime | None) -> None:
    """``/api/app`` tells the page whether the server stops with its tabs; the other routes exist
    only then."""

    @app.get("/api/app")
    async def app_info():
        return {"stops_with_browser": lifetime is not None}

    if lifetime is None:
        return

    # async: answered on the event loop even when every worker thread is calculating
    @app.post("/api/tab/alive")
    async def tab_alive(tab: str):
        lifetime.alive(tab)
        return {}

    @app.post("/api/tab/bye")
    async def tab_bye(tab: str):
        lifetime.bye(tab)
        return {}

    @app.post("/api/shutdown")
    async def shutdown():
        waiting = lifetime.shut_down()
        return {"waiting": waiting}
