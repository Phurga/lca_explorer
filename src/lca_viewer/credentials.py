"""ecoinvent credentials, kept in the operating system's credential store.

They are stored with ``keyring``: Windows Credential Manager, the macOS Keychain, or the Secret
Service (GNOME Keyring, KWallet) on Linux. Never in a file of this repository or of the data
folder, never in a command line, a log or a page: the server hands them to the ``lca-viewer
ecoinvent`` process in its environment (``EI_USERNAME``, ``EI_PASSWORD``, the variables
``ecoinvent_interface`` reads), and the page only ever learns the user name.

Backends that would write the password to a plain file (``keyrings.alt``) are refused: without
a secure store, credentials are kept in memory for the session only.
"""

from __future__ import annotations

import os

SERVICE = "lca_viewer:ecoinvent"
USERNAME_ENTRY = "__username__"  # the user name, as a secret of its own: not every backend can list entries
ENV = ("EI_USERNAME", "EI_PASSWORD")


def _keyring():
    """The keyring module when its backend stores secrets securely, else None."""
    try:
        import keyring
    except ImportError:
        return None
    backend = keyring.get_keyring()
    module = type(backend).__module__
    if module.startswith(("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")):
        return None
    if module.startswith("keyring.backends.chainer") and not getattr(backend, "backends", None):
        return None
    return keyring


def store() -> str | None:
    """Name of the secure store in use (e.g. "Windows Credential Manager"), or None."""
    kr = _keyring()
    if kr is None:
        return None
    name = type(kr.get_keyring()).__module__
    return {
        "keyring.backends.Windows": "Windows Credential Manager",
        "keyring.backends.macOS": "macOS Keychain",
        "keyring.backends.SecretService": "the Secret Service (GNOME Keyring)",
        "keyring.backends.kwallet": "KWallet",
    }.get(name, name)


def load() -> tuple[str, str] | None:
    """(username, password) from the environment (``EI_USERNAME``, ``EI_PASSWORD``), else from
    the credential store; None when there are none."""
    username, password = (os.environ.get(k) for k in ENV)
    if username and password:
        return username, password
    kr = _keyring()
    if kr is None:
        return None
    username = kr.get_password(SERVICE, USERNAME_ENTRY)
    password = username and kr.get_password(SERVICE, username)
    return (username, password) if password else None


def stored_username() -> str | None:
    kr = _keyring()
    return kr.get_password(SERVICE, USERNAME_ENTRY) if kr else None


def save(username: str, password: str) -> None:
    kr = _keyring()
    if kr is None:
        raise ValueError("No secure credential store on this computer (keyring): the credentials can't be remembered")
    forget()
    kr.set_password(SERVICE, username, password)
    kr.set_password(SERVICE, USERNAME_ENTRY, username)


def forget() -> None:
    kr = _keyring()
    if kr is None:
        return
    from keyring.errors import PasswordDeleteError

    username = kr.get_password(SERVICE, USERNAME_ENTRY)
    for entry in (username, USERNAME_ENTRY):
        if entry:
            try:
                kr.delete_password(SERVICE, entry)
            except PasswordDeleteError:
                pass


def environment(username: str, password: str) -> dict[str, str]:
    """The variables giving the credentials to a ``lca-viewer ecoinvent`` process."""
    return dict(zip(ENV, (username, password)))


def check(username: str, password: str) -> list[str]:
    """Log in to ecoinvent; returns the versions this account can download. ValueError when
    the login fails."""
    import ecoinvent_interface as ei

    try:
        return releases(ei.Settings(username=username, password=password))
    except Exception as e:  # requests.HTTPError, missing values…
        response = getattr(e, "response", None)
        if response is not None and response.status_code in (400, 401, 403):
            raise ValueError("ecoinvent refused this user name and password") from None
        raise ValueError(f"Could not log in to ecoinvent: {e}") from None


def releases(settings) -> list[str]:
    import ecoinvent_interface as ei

    versions = ei.EcoinventRelease(settings).list_versions()
    return sorted(versions, key=_version_key, reverse=True)


def _version_key(version: str):
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))
