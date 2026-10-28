"""The UI's static files are served with types browsers accept, whatever the machine says."""

import mimetypes
import subprocess
import sys


def test_scripts_are_javascript_even_when_the_registry_says_otherwise():
    # A fresh process, as on a Windows machine whose registry maps .js to text/plain and lacks .mjs
    code = """
import mimetypes
mimetypes.init()
mimetypes.add_type("text/plain", ".js")
mimetypes.types_map.pop(".mjs", None)
import lca_viewer.server
from fastapi.responses import FileResponse
from lca_viewer.server import STATIC
for name in ["app.js", "vendor/preact.mjs", "style.css"]:
    print(FileResponse(STATIC / name).media_type)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["text/javascript", "text/javascript", "text/css"]


def test_server_module_registers_the_types():
    import lca_viewer.server  # noqa: F401

    assert mimetypes.guess_type("x.mjs")[0] == "text/javascript"
    assert mimetypes.guess_type("x.js")[0] == "text/javascript"
