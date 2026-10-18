"""Exercise the installed runtime's real HTTP paths without a live AI call."""
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import fitz
from PIL import Image

command = [sys.executable, "-m", "boardbrain_v2"] if sys.argv[1] == "--source" else [sys.argv[1]]
with tempfile.TemporaryDirectory() as folder:
    process = subprocess.Popen(command + ["--serve", "--data-dir", folder])
    try:
        runtime = Path(folder) / "runtime.json"
        deadline = time.monotonic() + 45
        while not runtime.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Packaged runtime did not start")
            time.sleep(.1)
        info = json.loads(runtime.read_text())
        base = f"http://127.0.0.1:{info['port']}"

        def request(path, data=None, binary=False):
            req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                         headers={"Authorization": "Bearer " + info["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read() if binary else json.load(response)

        state = request("/api/state")
        assert state["cases"] == [] and state["version"].startswith("0.2.")
        assert b"BoardBrain" in request("/", binary=True)
        assert b"new-reference" in request("/app.js", binary=True)
        try:
            urllib.request.urlopen(base + "/api/state")
            raise AssertionError("Unauthenticated API access was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        case = request("/api/cases", {"kind": "reference", "title": "Synthetic packaged reference", "board_id": "TEST-001"})
        cid = case["id"]
        assert case["kind"] == "reference"
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), "U1 USB_D+ synthetic schematic reference for packaging tests")
            pdf = doc.tobytes()
        image = io.BytesIO()
        Image.new("RGB", (40, 40), "green").save(image, "PNG")
        sources = []
        for filename, role, content, expected in [
            ("test.csv", "boardview", b"ref,pin,net\nU1,1,USB_D+\nU1,2,USB_D-\n", "parsed"),
            ("test.pdf", "schematic", pdf, "ready"),
            ("test.png", "photo", image.getvalue(), "ready"),
        ]:
            result = request("/api/upload", {"case_id": cid, "filename": filename, "role": role, "content": base64.b64encode(content).decode()})
            sid = result["source"]["id"]
            deadline = time.monotonic() + 30
            while True:
                saved = request("/api/case?id=" + cid)
                source = next(s for s in saved["sources"] if s["id"] == sid)
                if source["status"] not in ("queued", "processing"):
                    assert source["status"] == expected, source["info"]
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Packaged import timed out")
                time.sleep(.1)
            sources.append(sid)
        results = request("/api/lookup?" + urllib.parse.urlencode({"case_id": cid, "q": "USB_D+"}))
        assert {r["kind"] for r in results} == {"pin", "document"}
        assert request(f"/api/page?case_id={cid}&id={sources[1]}&page=1", binary=True).startswith(b"\x89PNG")
        assert request(f"/api/photo?case_id={cid}&id={sources[2]}", binary=True).startswith(b"\xff\xd8")
        repair = request("/api/cases", {"title": "Synthetic repair", "symptom": "Test only", "reference_id": cid})
        assert repair["kind"] == "repair" and len(repair["sources"]) == 3
        assert not any(s["scope_confirmed"] for s in repair["sources"])
        print("Runtime check passed: startup, authentication, reference workspace, boardview/PDF/photo import, exact search, page/photo rendering and repair reuse.")
    finally:
        process.terminate()
        process.wait(timeout=20)
