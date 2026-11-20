"""End-to-end flows with mock hardware (no printer, scanner or coin acceptor needed)."""
import json
import shutil
import threading
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
    ses = m.session
    m.upload_received(d / "input.pdf", "big.pdf")
    wait_for(m, "phone_wait")                      # back to the phone screen, same session
    assert "Too many pages" in m.message
    assert m.session is ses and "Too many pages" in ses.message
    upload_again = m.new_job_dir()
    make_pdf(upload_again / "input.pdf", 3)
    m.upload_received(upload_again / "input.pdf", "small.pdf")
    wait_for(m, "choose")
    assert m.job["pages"] == 3


def test_bad_file_from_usb_still_errors(machine):
    m = machine
    with m.lock:
        m._set("converting")
    d = m.new_job_dir()
    (d / "input.pdf").write_bytes(b"not a pdf")
    m._prepare(d / "input.pdf", "x.pdf")
    assert m.screen == "error"


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
    assert m.screen == "scan_review" and m.scans == []   # opens on the preview
    m.scan_more()                                            # "Scan"
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
    assert m.screen == "scan_review" and m.scans == []   # opens on the preview
    m.scan_more()                                            # "Scan"
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
    assert m.screen == "scan_review" and m.scans == []   # opens on the preview
    m.scan_more()                                            # "Scan"
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
    assert m.screen == "scan_review" and m.scans == []   # opens on the preview
    m.scan_more()                                            # "Scan"
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
    c.post("/api/scan/more")
    wait_for(m, "scan_review")
    r = c.get("/api/scan/preview/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.post("/api/scan/rescan", json={"index": 0}).status_code == 200
    wait_for(m, "scan_review")
    assert c.post("/api/scan/rescan", json={"index": "x"}).status_code == 400

def test_full_size_scan_for_zoom(machine):
    m = machine
    m.scan_start("copy")
    assert m.screen == "scan_review" and m.scans == []   # opens on the preview
    m.scan_more()                                            # "Scan"
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


# ---------- new: scan opens on the preview ----------

def test_scan_opens_on_empty_preview(machine):
    m = machine
    m.scan_start("copy")
    snap = m.snapshot()
    assert snap["screen"] == "scan_review" and snap["scans"] == 0 and snap["scan_quote"] is None
    with pytest.raises(MachineError):
        m.scan_done()                                  # nothing scanned yet
    m.scan_more()
    assert m.screen == "scanning"
    seen = set()
    while m.screen == "scanning":
        if m.progress is not None:
            seen.add(m.progress)
        time.sleep(0.02)
    assert m.screen == "scan_review" and len(m.scans) == 1
    assert len(seen) > 1 and max(seen) <= 100          # progress reported while scanning
    assert m.progress is None


def test_first_scan_failure_returns_to_preview(machine):
    m = machine

    def broken(out, on_progress=None):
        raise RuntimeError("lid open")
    m.scanner.scan = broken
    m.scan_start("copy")
    m.scan_more()
    wait_for(m, "scan_review")
    assert m.scans == [] and "failed" in m.message


# ---------- new: copies ----------

def test_copies_change_price_and_respect_page_limit(machine):
    m = machine
    upload(m, 7)
    assert m.snapshot()["job"]["copies"] == 1
    assert m.snapshot()["job"]["max_copies"] == 7            # 50 // 7
    m.set_copies(3)                                          # before choosing a mode: allowed
    m.set_mode("bw")
    assert m.job["price"] == 15                              # 21 pages, P5 per 10
    m.set_copies(2)
    assert m.job["price"] == 10                              # 14 pages
    for bad in (0, -1, 8, "2", True):
        with pytest.raises(MachineError):
            m.set_copies(bad)
    assert m.job["copies"] == 2
    m.coin(10)
    m.confirm()
    wait_for(m, "done")
    assert len(list((m.tmp / "out").glob("*bw_7p_x2.pdf"))) == 1
    pay = [json.loads(l) for l in (m.tmp / "var" / "ledger.log").read_text().splitlines()
           if '"pay"' in l][0]
    assert pay["copies"] == 2


def test_no_copies_for_scan_to_usb(machine):
    m = machine
    m.scan_start("usb")
    m.scan_more()
    wait_for(m, "scan_review")
    m.scan_done()
    with pytest.raises(MachineError):
        m.set_copies(2)


# ---------- new: phone sessions ----------

def test_phone_session_is_random_and_ends(machine):
    m = machine
    m.phone_start()
    s1 = m.session
    wait_until(lambda: s1.state == "ready")
    snap = m.snapshot()["phone"]
    assert snap["ssid"] == s1.ssid and snap["password"] == s1.password and not snap["connected"]
    lo, hi = m.cfg["server"]["upload_port_range"]
    assert lo <= s1.port < hi and len(s1.password) == 10
    m.cancel()
    assert m.session is None and m.snapshot()["phone"] is None
    m.phone_start()
    s2 = m.session
    assert (s2.ssid, s2.password, s2.token) != (s1.ssid, s1.password, s1.token)
    assert not m.accepting_upload(s1)                        # old session is dead


def wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met")


def test_phone_upload_one_device_only(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import upload as up
    m = machine
    m.phone_start()
    ses = m.session
    web = Path("web")
    captive = TestClient(up.create_captive(ses))
    r = captive.get("/generate_204", follow_redirects=False)        # Android probe
    assert r.status_code == 302 and r.headers["location"] == ses.upload_url
    r = captive.get("/hotspot-detect.html", follow_redirects=False)  # iPhone probe
    assert r.status_code == 302

    app = up.create(m, m.cfg, web, ses)
    phone = TestClient(app, client=("10.42.0.20", 5000))
    other = TestClient(app, client=("10.42.0.21", 5000))
    assert phone.get("/").status_code == 404                         # token required
    assert phone.get("/wrongtoken/").status_code == 404
    assert phone.get(f"/{ses.token}/").status_code == 200            # claims the session
    assert m.snapshot()["phone"]["connected"]
    assert other.get(f"/{ses.token}/").status_code == 403
    assert other.get(f"/{ses.token}/api/status").status_code == 403

    pdf = m.tmp / "doc.pdf"
    make_pdf(pdf, 2)
    with open(pdf, "rb") as f:
        r = phone.post(f"/{ses.token}/api/upload", files={"file": ("doc.pdf", f, "application/pdf")})
    assert r.status_code == 200, r.text
    wait_for(m, "choose")
    assert m.session is None                                         # session over after upload
    with open(pdf, "rb") as f:                                       # link can't be reused
        r = phone.post(f"/{ses.token}/api/upload", files={"file": ("doc.pdf", f, "application/pdf")})
    assert r.status_code == 409


def test_hotspot_failure_shows_error(machine):
    m = machine

    class Broken:
        def up(self, ssid, password):
            raise RuntimeError("no wifi")

        def down(self):
            pass
    m.phone.hotspot = Broken()
    m.phone_start()
    wait_for(m, "error")
    assert "Wi-Fi" in m.message and m.session is None


def test_wifi_qr_follows_session(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import kiosk
    m = machine
    c = TestClient(kiosk.create(m, m.cfg, Path("web")))
    assert c.get("/api/qr/wifi").status_code == 404                  # no session
    c.post("/api/phone")
    r = c.get("/api/qr/wifi")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_phone_page_follows_file_to_the_machine(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import upload as up
    m = machine
    m.phone_start()
    ses = m.session
    phone = TestClient(up.create(m, m.cfg, Path("web"), ses), client=("10.42.0.20", 5000))
    st = lambda: phone.get(f"/{ses.token}/api/status").json()
    assert st()["stage"] == "waiting" and st()["accepting"]

    # hold conversion so we can see "processing"
    gate = threading.Event()
    real_count = m._prepare

    def slow_prepare(*a, **k):
        gate.wait(5)
        return real_count(*a, **k)
    m._prepare = slow_prepare
    pdf = m.tmp / "doc.pdf"
    make_pdf(pdf, 2)
    with open(pdf, "rb") as f:
        assert phone.post(f"/{ses.token}/api/upload",
                          files={"file": ("doc.pdf", f, "application/pdf")}).status_code == 200
    assert m.session is ses                        # still open while converting
    assert st()["stage"] == "processing" and not st()["accepting"]
    gate.set()
    wait_for(m, "choose")
    assert m.session is None and st()["stage"] == "ready"


def test_phone_page_sees_cancel_and_failure(machine):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from app import upload as up
    m = machine
    m.phone_start()
    ses = m.session
    phone = TestClient(up.create(m, m.cfg, Path("web"), ses), client=("10.42.0.20", 5000))
    assert phone.get(f"/{ses.token}/").status_code == 200      # phone opened the page
    m.cancel()
    assert phone.get(f"/{ses.token}/api/status").json()["stage"] == "expired"

    m.phone_start()
    ses = m.session
    phone = TestClient(up.create(m, m.cfg, Path("web"), ses), client=("10.42.0.20", 5000))
    bad = m.tmp / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with open(bad, "rb") as f:
        phone.post(f"/{ses.token}/api/upload", files={"file": ("bad.pdf", f, "application/pdf")})
    wait_until(lambda: m.screen == "phone_wait" and m.message)
    s = phone.get(f"/{ses.token}/api/status").json()
    assert s["stage"] == "waiting" and s["accepting"] and s["message"]   # page offers a retry
    good = m.tmp / "good.pdf"
    make_pdf(good, 2)
    with open(good, "rb") as f:                                         # same session, same phone
        assert phone.post(f"/{ses.token}/api/upload",
                          files={"file": ("good.pdf", f, "application/pdf")}).status_code == 200
    wait_for(m, "choose")
    assert phone.get(f"/{ses.token}/api/status").json()["stage"] == "ready"