"""End-to-end flows with mock hardware (no printer, scanner or coin acceptor needed)."""
import json
import shutil
import time

import pytest
from pypdf import PdfWriter

from app.config import load_config
from app.hw.mock import FilePrinter, MockScanner
from app.machine import Machine, MachineError


def wait_for(m, *screens, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if m.screen in screens:
            return
        time.sleep(0.05)
    raise AssertionError(f"stuck on {m.screen!r} ({m.message}), wanted {screens}")


def make_pdf(path, pages):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    with open(path, "wb") as f:
        w.write(f)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setenv("PISO_CONFIG", "config.toml")
    cfg = load_config()
    cfg["paths"] = {"work_dir": str(tmp_path / "work"), "data_dir": str(tmp_path / "var")}
    cfg["printer"]["out_dir"] = str(tmp_path / "out")
    cfg["usb"]["fake_drive"] = str(tmp_path / "usb")
    (tmp_path / "usb").mkdir()
    m = Machine(cfg, FilePrinter(cfg["printer"]), MockScanner(cfg["scanner"]))
    m.tmp = tmp_path
    return m


def upload(m, pages):
    m.phone_start()
    d = m.new_job_dir()
    make_pdf(d / "input.pdf", pages)
    m.upload_received(d / "input.pdf", "homework.pdf")
    wait_for(m, "choose")


def test_phone_print_flow(machine):
    m = machine
    upload(m, 7)
    with pytest.raises(MachineError):
        m.confirm()                       # no mode yet
    m.set_mode("color")
    assert m.job["price"] == 10
    m.coin(5)
    with pytest.raises(MachineError):
        m.confirm()                       # not enough
    m.coin(10)                            # overpay by 5, forfeited
    job_dir = m.job["dir"]
    m.confirm()
    assert m.paid == 0
    wait_for(m, "done")
    assert not job_dir.exists()           # file deleted after printing
    assert len(list((m.tmp / "out").glob("*color_7p.pdf"))) == 1
    events = [json.loads(l) for l in (m.tmp / "var" / "ledger.log").read_text().splitlines()]
    pay = [e for e in events if e["event"] == "pay"][0]
    assert pay["price"] == 10 and pay["forfeit"] == 5


def test_paid_survives_restart(machine):
    m = machine
    m.coin(5)
    m.coin(1)
    m2 = Machine(m.cfg, m.printer, m.scanner)   # simulate power cut + reboot
    assert m2.paid == 6


def test_upload_rejected_when_not_waiting(machine):
    d = machine.new_job_dir()
    make_pdf(d / "input.pdf", 1)
    with pytest.raises(MachineError):
        machine.upload_received(d / "input.pdf", "x.pdf")
    assert not d.exists()


def test_too_many_pages(machine):
    m = machine
    m.phone_start()
    d = m.new_job_dir()
    make_pdf(d / "input.pdf", 51)
    m.upload_received(d / "input.pdf", "big.pdf")
    wait_for(m, "error")
    assert "Too many pages" in m.message


def test_paper_jam_gives_code(machine):
    m = machine
    upload(m, 2)
    m.set_mode("bw")
    m.coin(5)
    (m.tmp / "out" / "JAM").touch()
    m.confirm()
    wait_for(m, "error")
    assert "operator" in m.message


def test_usb_print(machine):
    m = machine
    make_pdf(m.tmp / "usb" / "notes.pdf", 3)
    m.usb_open()
    files = m.usb_files()
    assert [f["name"] for f in files] == ["notes.pdf"]
    m.usb_select(files[0]["path"])
    wait_for(m, "choose")
    assert m.job["pages"] == 3
    job_dir = m.job["dir"]
    m.cancel()
    assert m.screen == "idle" and not job_dir.exists()
    with pytest.raises(MachineError):
        m.confirm()


def test_usb_rejects_outside_path(machine):
    machine.usb_open()
    with pytest.raises(MachineError):
        machine.usb_select("/etc/passwd")


def test_copy_flow(machine):
    m = machine
    m.scan_start("copy")
    wait_for(m, "scan_review")
    m.scan_more()
    wait_for(m, "scan_review")
    m.scan_done()
    assert m.job["pages"] == 2 and m.job["action"] == "print"
    m.set_mode("bw")
    m.coin(5)
    m.confirm()
    wait_for(m, "done")
    assert len(list((m.tmp / "out").glob("*bw_2p.pdf"))) == 1


def test_scan_to_usb(machine):
    m = machine
    m.scan_start("usb")
    wait_for(m, "scan_review")
    m.scan_done()
    assert m.job["price"] == 5
    m.coin(5)
    m.confirm()
    wait_for(m, "done")
    assert len(list((m.tmp / "usb").glob("scan_*.pdf"))) == 1


def test_scan_to_usb_costs_bw_price(machine):
    m = machine
    m.cfg["price"].pop("scan", None)
    m.scan_start("usb")
    wait_for(m, "scan_review")
    for _ in range(10):
        m.scan_more()
        wait_for(m, "scan_review")
    assert m.snapshot()["scan_quote"] == {"scan": 10}     # 11 pages, bw = P5 per 10
    m.scan_done()
    assert m.job["price"] == 10


def test_rescan_replaces_page_for_free(machine):
    m = machine
    m.scan_start("copy")
    wait_for(m, "scan_review")
    m.scan_more()
    wait_for(m, "scan_review")
    first, second = list(m.scans)
    assert m.scan_preview(0).exists()
    m.coin(5)
    m.scan_rescan(0)
    assert m.snapshot()["rescan_index"] == 0
    wait_for(m, "scan_review")
    assert len(m.scans) == 2 and m.scans[1] == second and m.scans[0] != first
    assert not first.exists() and not first.with_suffix(".jpg").exists()
    assert m.scan_preview(0).exists()
    assert m.paid == 5                                   # rescanning charged nothing
    assert m.snapshot()["scan_quote"] == {"color": 5, "bw": 5}
    with pytest.raises(MachineError):
        m.scan_rescan(5)
    with pytest.raises(MachineError):
        m.scan_preview(5)
    m.scan_done()
    assert m.job["pages"] == 2


def test_preview_endpoint(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import kiosk
    m = machine
    c = TestClient(kiosk.create(m, m.cfg, Path("web")))
    assert c.get("/api/scan/preview/0").status_code == 400
    c.post("/api/scan/start", json={"action": "copy"})
    wait_for(m, "scan_review")
    r = c.get("/api/scan/preview/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.post("/api/scan/rescan", json={"index": 0}).status_code == 200
    wait_for(m, "scan_review")
    assert c.post("/api/scan/rescan", json={"index": "x"}).status_code == 400

def test_full_size_scan_for_zoom(machine):
    m = machine
    m.scan_start("copy")
    wait_for(m, "scan_review")
    assert m.scan_preview(0, full=True).suffix == ".png"
    assert m.scan_preview(0).suffix == ".jpg"


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler (pdftoppm)")
def test_job_preview_renders_pages(machine):
    from PIL import Image
    m = machine
    upload(m, 3)
    thumb = m.job_preview(2)
    full = m.job_preview(2, full=True)
    assert thumb.exists() and full.exists() and thumb != full
    assert max(Image.open(thumb).size) == 600 and max(Image.open(full).size) == 1600
    assert m.job_preview(2) == thumb                     # cached, not rendered again
    with pytest.raises(MachineError):
        m.job_preview(3)                                 # only 3 pages
    job_dir = m.job["dir"]
    m.cancel()
    assert not job_dir.exists()                          # previews deleted with the job
    with pytest.raises(MachineError):
        m.job_preview(0)


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler (pdftoppm)")
def test_job_preview_endpoint(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import kiosk
    m = machine
    c = TestClient(kiosk.create(m, m.cfg, Path("web")))
    assert c.get("/api/job/preview/0").status_code == 400     # no job loaded
    upload(m, 2)
    assert m.snapshot()["job"]["id"] == m.job["dir"].name
    r = c.get("/api/job/preview/1?full=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"