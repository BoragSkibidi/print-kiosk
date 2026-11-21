"""Kiosk API + touchscreen UI. Bound to 127.0.0.1 only: phones can never reach it."""
import io
from pathlib import Path

import qrcode
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .machine import MachineError, Machine


def _wifi_escape(s: str) -> str:
    for ch in '\\;,:"':
        s = s.replace(ch, "\\" + ch)
    return s


def _qr_png(data) -> Response:
    buf = io.BytesIO()
    qrcode.make(data, border=2).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")

# api for kiossk
def create(machine: Machine, cfg, web: Path) -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    m = machine

    @app.exception_handler(MachineError)
    async def machine_error(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)
    ok = {"ok": True}

    @app.get("/api/state")
    def state():
        return m.snapshot()

    @app.post("/api/phone")
    def phone():
        m.phone_start(); return ok

    @app.post("/api/usb")
    def usb_open():
        m.usb_open(); return ok

    @app.get("/api/usb/files")
    def usb_files():
        return m.usb_files()

    @app.post("/api/usb/select")
    def usb_select(body: dict = Body(...)):
        m.usb_select(str(body.get("path", ""))); return ok

    @app.post("/api/scan/start")
    def scan_start(body: dict = Body(...)):
        m.scan_start(body.get("action")); return ok

    @app.post("/api/scan/more")
    def scan_more():
        m.scan_more(); return ok

    @app.post("/api/scan/rescan")
    def scan_rescan(body: dict = Body(...)):
        m.scan_rescan(body.get("index")); return ok

    cache = {"Cache-Control": "private, max-age=600"}

    @app.get("/api/scan/preview/{index}")
    def scan_preview(index: int, full: int = 0):
        path = m.scan_preview(index, bool(full))
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png" if full else "image/jpeg", headers=cache)

    @app.get("/api/job/preview/{index}")
    def job_preview(index: int, full: int = 0):
        path = m.job_preview(index, bool(full))
        return FileResponse(path, media_type="image/jpeg", headers=cache)

    @app.post("/api/scan/done")
    def scan_done():
        m.scan_done(); return ok

    @app.post("/api/mode")
    def mode(body: dict = Body(...)):
        m.set_mode(body.get("mode")); return ok

    @app.post("/api/copies")
    def copies(body: dict = Body(...)):
        m.set_copies(body.get("copies")); return ok

    @app.post("/api/confirm")
    def confirm():
        m.confirm(); return ok

    @app.post("/api/cancel")
    def cancel():
        m.cancel(); return ok

    @app.get("/api/qr/url")
    def qr_url():
        # QR 2: this session's upload page (random port + token)
        ses = m.session
        if not ses:
            raise HTTPException(404)
        png = _qr_png(ses.upload_url)
        png.headers["Cache-Control"] = "no-store"
        return png

    @app.get("/api/qr/wifi")
    def qr_wifi():
        # Made fresh for the current phone session: new SSID + password every time.
        ses = m.session
        if not ses:
            raise HTTPException(404)
        png = _qr_png(f"WIFI:T:WPA;S:{_wifi_escape(ses.ssid)};P:{_wifi_escape(ses.password)};;")
        png.headers["Cache-Control"] = "no-store"
        return png

    if cfg["hardware"]["coin"] == "mock":
        @app.post("/api/dev/coin")
        def dev_coin(body: dict = Body(...)):
            value = int(body.get("value", 0))
            if value not in (1, 5, 10, 20):
                raise HTTPException(400, "coin must be 1, 5, 10 or 20")
            m.coin(value); 
            return ok

    @app.get("/")
    def page():
        return FileResponse(web / "kiosk" / "kiosk.html")

    app.mount("/", StaticFiles(directory= web / "kiosk", html=True), name="kiosk")
    return app