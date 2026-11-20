"""Phone-facing apps, one pair per phone session (see phone.py).

captive: every request on the captive port (80 on the Pi) -> redirect to the upload page.
         Phones probe a known URL right after joining Wi-Fi; getting a redirect instead
         of the expected answer makes them open the page automatically.
upload:  the upload page + API, only under /<token>/ and only for the first phone.
"""
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .machine import MachineError

BUSY_PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Kiosk Print</title><body style="font-family:sans-serif;padding:24px">
<h2>Another phone is using this machine</h2>
<p>Only one phone can upload at a time. Wait for it to finish, then tap
<b>Print from phone</b> on the machine again.</p></body>"""


def create_captive(session):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
    def anything(path: str):
        # 302, not 204/200: that is what tells Android/iOS "this Wi-Fi has a sign-in page"
        return RedirectResponse(session.upload_url, status_code=302,
                                headers={"Cache-Control": "no-store"})

    return app


def create(machine, cfg, web: Path, session):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    L = cfg["limits"]
    max_bytes = L["max_upload_mb"] * 1024 * 1024
    allowed = set(L["allowed_ext"])
    base = f"/{session.token}"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path == base:
            return RedirectResponse(base + "/")
        if not path.startswith(base + "/"):
            return JSONResponse({"detail": "Not found"}, 404)       # wrong / old token
        if not machine.phone_claim(session, request.client.host):
            if "/api/" in path:
                return JSONResponse({"detail": "Another phone is using this machine"}, 403)
            return HTMLResponse(BUSY_PAGE, 403)
        # Reject big uploads before FastAPI spools them to disk.
        if path == base + "/api/upload":
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length > max_bytes + 64 * 1024:
                return JSONResponse({"detail": f"File is too big (max {L['max_upload_mb']} MB)"}, 413)
        return await call_next(request)

    @app.get(base + "/api/status")
    def status():
        stage = machine.phone_stage(session)
        return {"accepting": stage == "waiting", "stage": stage,
                "message": session.message if stage == "failed" else "",
                "max_mb": L["max_upload_mb"], "allowed": sorted(allowed)}

    @app.post(base + "/api/upload")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in allowed:
            raise HTTPException(400, "Only PDF, Word and PowerPoint files are allowed")
        if not machine.accepting_upload(session):
            raise HTTPException(409, "This upload link has expired. Tap 'Print from phone' on the machine.")
        d = machine.new_job_dir()
        dest = d / ("input" + ext)
        size = 0
        try:
            with open(dest, "wb") as f:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, f"File is too big (max {L['max_upload_mb']} MB)")
                    f.write(chunk)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        try:
            machine.upload_received(dest, name, session)
        except MachineError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    app.mount(base, StaticFiles(directory=web / "upload", html=True), name="upload")
    return app