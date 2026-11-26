# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server started on your own computer stops with its last browser tab (lifetime.Lifetime)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lca_viewer.lifetime import Lifetime, add_routes


def make(busy=()):
    stops = []
    lifetime = Lifetime(lambda: stops.append(True), lambda: list(busy), grace=10, silence=150)
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
    lifetime, stops = make()
    lifetime.alive("a")  # heard of at time.monotonic(), then never again
    heard = lifetime.tabs["a"]
    since = lifetime.check(heard + 151, None)
    assert "a" not in lifetime.tabs and since is not None
    lifetime.check(heard + 161, since)
    assert stops


def test_jobs_under_way_finish_first():
    busy = ["Import of ecoinvent"]
    lifetime, stops = make(busy)
    lifetime.alive("a")
    assert lifetime.shut_down() == busy
    lifetime.check(0.0, None)
    assert not stops
    busy.clear()
    lifetime.check(1.0, None)
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
    assert "t1" in lifetime.tabs
    client.post("/api/tab/bye", params={"tab": "t1"})
    assert not lifetime.tabs
    assert client.post("/api/shutdown").json() == {"waiting": []}
    lifetime.check(0.0, None)
    assert stops
