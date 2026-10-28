"""ecoinvent credentials: kept in the credential store (here an in-memory keyring), never shown."""

import pytest

keyring = pytest.importorskip("keyring")
from keyring.backend import KeyringBackend  # noqa: E402

from lca_viewer import credentials, sources  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.secrets = {}

    def get_password(self, service, username):
        return self.secrets.get((service, username))

    def set_password(self, service, username, password):
        self.secrets[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if self.secrets.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture
def store(monkeypatch):
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    for name in credentials.ENV:
        monkeypatch.delenv(name, raising=False)
    yield backend
    keyring.set_keyring(previous)


def test_save_load_forget(store):
    assert credentials.load() is None
    credentials.save("me@lab.org", "s3cret")
    assert credentials.load() == ("me@lab.org", "s3cret")
    assert credentials.stored_username() == "me@lab.org"
    credentials.save("other", "pw")  # replaces the previous account
    assert credentials.load() == ("other", "pw")
    assert ("lca_viewer:ecoinvent", "me@lab.org") not in store.secrets
    credentials.forget()
    assert credentials.load() is None and store.secrets == {}
    credentials.forget()  # nothing to forget


def test_environment_first(store, monkeypatch):
    credentials.save("stored", "pw")
    for name, value in credentials.environment("from-env", "pw2").items():
        monkeypatch.setenv(name, value)
    assert credentials.load() == ("from-env", "pw2")


def test_insecure_backend_refused(monkeypatch):
    from keyring.backends import fail

    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        assert credentials.store() is None
        with pytest.raises(ValueError):
            credentials.save("me", "pw")
    finally:
        keyring.set_keyring(previous)


def test_api_never_returns_the_password(store, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    monkeypatch.setattr(credentials, "check", lambda u, p: ["3.11", "3.10.1"] if p == "good" else (_ for _ in ()).throw(
        ValueError("ecoinvent refused this user name and password")))
    client = TestClient(create_library(tmp_path / "data"))
    assert client.get("/api/ecoinvent").json()["username"] is None
    r = client.post("/api/ecoinvent/login", json={"username": "me", "password": "bad"})
    assert r.status_code == 400 and "refused" in r.json()["detail"]
    assert store.secrets == {}

    r = client.post("/api/ecoinvent/login", json={"username": "me", "password": "good"})
    assert r.json()["versions"] == ["3.11", "3.10.1"] and r.json()["remembered"]
    assert credentials.load() == ("me", "good")
    status = client.get("/api/ecoinvent").json()
    assert status["username"] == "me" and "good" not in str(status)

    client.post("/api/ecoinvent/logout")
    assert credentials.load() is None
    r = client.post("/api/ecoinvent/login", json={"username": "me", "password": "good", "remember": False})
    assert not r.json()["remembered"] and store.secrets == {}  # memory only
    assert client.get("/api/ecoinvent").json()["username"] == "me"


def test_import_passes_credentials_in_the_environment(store, tmp_path, monkeypatch):
    import subprocess

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    credentials.save("me", "s3cret")
    seen = {}

    class Done:
        stdout = iter(["imported ecoinvent-3.11-cutoff into project ei\n"])

        def __init__(self, command, env, **kwargs):
            seen.update(command=command, env=env)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    monkeypatch.setattr(sources, "brightway_projects", lambda folder: [{"name": "ei", "databases": []}])
    client = TestClient(create_library(tmp_path / "data"))
    folder = str(tmp_path / "bw")
    assert client.post("/api/ecoinvent/import", json={"folder": folder, "project": "new", "version": "3.11"}).status_code == 404

    def run(body):
        job = client.post("/api/ecoinvent/import", json={"folder": folder, "project": "ei", "version": "3.11", **body}).json()
        for _ in range(100):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
        return job

    job = run({})
    assert job["status"] == "done", job
    assert "s3cret" not in " ".join(seen["command"]) and "s3cret" not in str(job)
    assert seen["env"]["EI_USERNAME"] == "me" and seen["env"]["EI_PASSWORD"] == "s3cret"
    assert "--biosphere" not in seen["command"]  # the release's own biosphere and methods
    Done.stdout = iter(["imported\n"])
    assert run({"biosphere": "biosphere3"})["status"] == "done"
    assert seen["command"][-2:] == ["--biosphere", "biosphere3"]
