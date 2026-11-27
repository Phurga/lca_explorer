# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server started on your own computer stops with its last browser tab (lifetime.Lifetime)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lca_viewer.lifetime import Lifetime, add_routes


def make(busy=()):
    stops = []
    lifetime = Lifetime(lambda: stops.append(True), lambda: list(busy), grace=10, silence=30, silence_hidden=150)
    return lifetime, stops


def test_waits_for_the_first_tab_but_not_for_ever():
    lifetime, stops = make()
    lifetime.started = 0.0
    assert lifetime.check(100.0, None) is None  # the browser is starting
    assert not stops
    lifetime.check(121.0, None)  # it never opened the app
    assert stops


def test_a_tab_that_opened_once_keeps_it_running():
    lifetime, stops = make()
    lifetime.started = 0.0
    lifetime.alive("a")
    lifetime.tabs["a"] = 500.0
    lifetime.check(600.0, None)
    assert not stops


def test_stops_some_time_after_the_last_tab_closes():
    lifetime, stops = make()
    lifetime.alive("a")
    lifetime.alive("b")
    lifetime.bye("a")
    assert lifetime.check(0.0, None) is None  # b is still open
    lifetime.bye("b")
    since = lifetime.check(100.0, None)
    assert since == 100.0 and not stops
    assert lifetime.check(105.0, since) == 100.0 and not stops
    lifetime.check(110.0, since)
    assert stops


def test_a_reload_comes_back_in_time():
    lifetime, stops = make()
    lifetime.alive("a")
    lifetime.bye("a")
    since = lifetime.check(100.0, None)
    lifetime.alive("a2")  # the page loaded again
    assert lifetime.check(120.0, since) is None
    assert not stops


def test_a_silent_tab_counts_as_closed():
    # Its goodbye was lost (a browser that quit, crashed, or closed it without unloading it)
    lifetime, stops = make()
    lifetime.alive("a")  # heard of at time.monotonic(), then never again
    heard = lifetime.tabs["a"]
    assert lifetime.check(heard + 25, None) is None  # a ping or two late: still open
    since = lifetime.check(heard + 31, None)
    assert "a" not in lifetime.tabs and since is not None
    lifetime.check(heard + 41, since)
    assert stops


def test_a_hidden_tab_has_longer_to_speak():
    # Browsers slow the timers of hidden tabs to once a minute
    lifetime, stops = make()
    lifetime.alive("a", hidden=True)
    heard = lifetime.tabs["a"]
    assert lifetime.check(heard + 120, None) is None and "a" in lifetime.tabs
    since = lifetime.check(heard + 151, None)
    assert "a" not in lifetime.tabs and "a" not in lifetime.hidden
    lifetime.check(heard + 161, since)
    assert stops


def test_a_tab_shown_again_is_waited_for_as_a_shown_one():
    lifetime, stops = make()
    lifetime.alive("a", hidden=True)
    lifetime.alive("a")  # on screen again
    assert "a" not in lifetime.hidden
    lifetime.check(lifetime.tabs["a"] + 31, None)
    assert "a" not in lifetime.tabs


def test_a_missed_ping_does_not_close_a_tab():
    # One ping lost (the network changing on waking up): the next one keeps the tab
    lifetime, stops = make()
    lifetime.alive("a")
    heard = lifetime.tabs["a"]
    assert lifetime.check(heard + 20, None) is None
    lifetime.alive("a")
    assert lifetime.check(lifetime.tabs["a"] + 25, None) is None
    assert not stops


def test_jobs_under_way_finish_first():
    busy = ["Import of ecoinvent"]
    lifetime, stops = make(busy)
    lifetime.alive("a")
    lifetime.bye("a")  # the last tab closed during an import
    since = lifetime.check(0.0, None)
    lifetime.check(20.0, since)
    assert not stops
    busy.clear()
    lifetime.check(21.0, since)
    assert stops


def test_routes():
    app = FastAPI()
    add_routes(app, None)
    assert TestClient(app).get("/api/app").json() == {"stops_with_browser": False}

    lifetime, stops = make()
    app = FastAPI()
    add_routes(app, lifetime)
    client = TestClient(app)
    assert client.get("/api/app").json() == {"stops_with_browser": True}
    client.post("/api/tab/alive", params={"tab": "t1"})
    assert "t1" in lifetime.tabs and "t1" not in lifetime.hidden
    client.post("/api/tab/alive", params={"tab": "t1", "hidden": "1"})
    assert "t1" in lifetime.hidden
    client.post("/api/tab/bye", params={"tab": "t1"})
    assert not lifetime.tabs and not lifetime.hidden
    # No route to shut down: closing the last tab does
    assert client.post("/api/shutdown").status_code == 404
    since = lifetime.check(0.0, None)
    lifetime.check(10.0, since)
    assert stops
