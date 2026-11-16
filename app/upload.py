"""Phone-facing app on the hotspot. Only the upload page and upload API live here."""
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .machine import MachineError


def create(machine, cfg, web: Path):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    L = cfg["limits"]
    max_bytes = L["max_upload_mb"] * 1024 * 1024
    allowed = set(L["allowed_ext"])

    @app.middleware("http")
    async def size_guard(request: Request, call_next):
        # Reject big uploads before FastAPI spools them to disk.
        if request.url.path == "/api/upload":
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length > max_bytes + 64 * 1024:
                return JSONResponse({"detail": f"File is too big (max {L['max_upload_mb']} MB)"}, 413)
        return await call_next(request)

    @app.get("/api/status")
    def status():
        return {"accepting": machine.accepting_upload()}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in allowed:
            raise HTTPException(400, "Only PDF, Word and PowerPoint files are allowed")
        if not machine.accepting_upload():
            raise HTTPException(409, "Machine busy. Tap 'Print from phone' on the machine first.")
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
            machine.upload_received(dest, name)
        except MachineError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    app.mount("/", StaticFiles(directory=web / "upload", html=True), name="upload")
    return app
