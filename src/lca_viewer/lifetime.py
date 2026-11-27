# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app started on your own computer stops with its browser tabs.

Each tab of the page says it is open (``/api/tab/alive``, every 10 s, from a worker so that a
dialog or a long calculation on the page does not hold it back) and that it closes
(``/api/tab/bye``, as it goes). Once no tab is left for :attr:`Lifetime.grace` seconds (a
reload is back before), the server stops, after the jobs under way (an import, a build)
finish. Closing the last tab is how the app is shut down: the page has no button for it.

The goodbye is not always heard: a browser that quits, crashes or is killed, or closes a tab
without unloading it, says nothing. So a tab not heard of for a while counts as closed:
:attr:`Lifetime.silence` seconds for a tab on screen, :attr:`Lifetime.silence_hidden` for a
hidden one (each ping says which), because browsers slow the timers of hidden tabs to once a
minute. A single ping that fails (the network changing on waking up) is followed by the next.

When no tab opens the app within :attr:`Lifetime.first` seconds (the browser did not open it),
the server stops too: started by lca_explorer, it runs without a window, and nothing else
would stop it.

Not used for a server reached through SSH: a tunnel that drops would stop it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class Lifetime:
    def __init__(self, stop: Callable[[], None], busy: Callable[[], list[str]] = lambda: [],
                 grace: float = 10.0, silence: float = 30.0, silence_hidden: float = 150.0, first: float = 120.0,
                 tick: float = 1.0):
        self.stop, self.busy = stop, busy
        self.grace, self.silence, self.silence_hidden, self.first, self.tick = grace, silence, silence_hidden, first, tick
        self.started = time.monotonic()
        self.tabs: dict[str, float] = {}  # tab -> when it was last heard of (time.monotonic)
        self.hidden: set[str] = set()  # the tabs that said they are hidden
        self.seen = False  # a tab opened once: until then (the browser starting) nothing stops
        self.stopped = False
        self._lock = threading.Lock()

    def alive(self, tab: str, hidden: bool = False) -> None:
        with self._lock:
            self.tabs[tab] = time.monotonic()
            (self.hidden.add if hidden else self.hidden.discard)(tab)
            self.seen = True

    def bye(self, tab: str) -> None:
        with self._lock:
            self.tabs.pop(tab, None)
            self.hidden.discard(tab)

    def check(self, now: float, empty_since: float | None) -> float | None:
        """One look: stops the server when it is time; when the tabs were all gone (None: some open)."""
        with self._lock:
            for tab, heard in list(self.tabs.items()):
                if now - heard > (self.silence_hidden if tab in self.hidden else self.silence):
                    del self.tabs[tab]
                    self.hidden.discard(tab)
            empty = self.seen and not self.tabs
        if empty and empty_since is None:
            empty_since = now
        elif not empty:
            empty_since = None
        never_opened = not self.seen and now - self.started >= self.first
        due = never_opened or (empty_since is not None and now - empty_since >= self.grace)
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
    async def tab_alive(tab: str, hidden: bool = False):
        lifetime.alive(tab, hidden)
        return {}

    @app.post("/api/tab/bye")
    async def tab_bye(tab: str):
        lifetime.bye(tab)
        return {}
