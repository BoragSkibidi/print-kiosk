"""End-to-end flows with mock hardware (no printer, scanner or coin acceptor needed)."""
import json
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
