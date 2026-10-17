"""Smoke the bundled runtime without an API key or customer files."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

with tempfile.TemporaryDirectory() as folder:
    process = subprocess.Popen([sys.argv[1], "--serve", "--data-dir", folder])
    try:
        runtime = Path(folder) / "runtime.json"
        deadline = time.monotonic() + 45
        while not runtime.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Packaged runtime did not start")
            time.sleep(.1)
        info = json.loads(runtime.read_text())
        request = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/state", headers={"Authorization": "Bearer " + info["token"]})
        with urllib.request.urlopen(request, timeout=5) as response:
            state = json.load(response)
        assert state["cases"] == []
        assert state["version"].startswith("0.2.")
        print("Packaged runtime started with an isolated empty workspace.")
    finally:
        process.terminate()
        process.wait(timeout=15)
