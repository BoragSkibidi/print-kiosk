"""The machine: one customer at a time, so this is one state variable, not a job list.

Screens: idle, phone_wait, usb, converting, scanning, scan_review, choose,
         printing, saving, done, error, out_of_service

Money: `paid` = coins inserted for the current job. Saved to disk after every
coin so a power cut doesn't lose it. Set to 0 when a job is paid for
(leftover is forfeited: no change). Every coin and job goes to ledger.log.
"""
import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import docs, usb
from .pricing import price as calc_price

log = logging.getLogger("piso.machine")


class MachineError(Exception):
    """Shown to the customer as-is."""


class Machine:
    def __init__(self, cfg, printer, scanner):
        self.cfg = cfg
        self.L = cfg["limits"]
        self.printer = printer
        self.scanner = scanner
        self.lock = threading.RLock()

        self.work = Path(cfg["paths"]["work_dir"]).resolve()
        if len(self.work.parts) < 3 or self.work == Path.home():
            raise ValueError(f"work_dir {self.work} looks unsafe to wipe")
        shutil.rmtree(self.work, ignore_errors=True)      # wipe leftovers on boot
        self.work.mkdir(parents=True)
        self.data = Path(cfg["paths"]["data_dir"]).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data / "state.json"
        self.ledger_file = self.data / "ledger.log"

        self.paid = self._load_paid()
        self.screen = "idle"
        self.message = ""
        self.since = time.monotonic()
        self.job = None            # dict while a file is loaded
        self.scans = []            # scanned page images (copy / scan to USB)
        self.scan_dir = None
        self.scan_action = None    # "copy" | "usb"
        self.printer_ok, self.printer_msg = True, ""
        self._last_check = 0.0

        self.ledger("boot", paid=self.paid)
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- persistence ----------
    def _load_paid(self):
        try:
            return int(json.loads(self.state_file.read_text())["paid"])
        except (OSError, ValueError, KeyError):
            return 0

    def _save_paid(self):
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"paid": self.paid}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_file)

    def ledger(self, event, **kv):
        line = json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "event": event, **kv})
        with open(self.ledger_file, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ---------- helpers ----------
    def _set(self, screen, message=""):
        self.screen, self.message, self.since = screen, message, time.monotonic()

    def _touch(self):
        self.since = time.monotonic()

    def _require(self, *screens):
        if self.screen not in screens:
            raise MachineError("Not available right now")

    def _spawn(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def new_job_dir(self):
        d = self.work / uuid.uuid4().hex[:8]
        d.mkdir()
        return d

    def _clear(self):
        if self.job:
            shutil.rmtree(self.job["dir"], ignore_errors=True)
            self.job = None
        if self.scan_dir:
            shutil.rmtree(self.scan_dir, ignore_errors=True)
            self.scan_dir = None
        self.scans, self.scan_action = [], None

    def snapshot(self):
        with self.lock:
            j = self.job
            price = j["price"] if j else None
            return {
                "screen": self.screen,
                "message": self.message,
                "paid": self.paid,
                "job": None if not j else {k: j[k] for k in ("name", "pages", "mode", "price", "action")},
                "enough": price is not None and self.paid >= price,
                "scans": len(self.scans),
                "scan_action": self.scan_action,
                "prices": self.cfg["price"],
                "usb_present": bool(usb.drives(self.cfg)),
                "mock_coins": self.cfg["hardware"]["coin"] == "mock",
                "hotspot": {"ssid": self.cfg["hotspot"]["ssid"],
                            "password": self.cfg["hotspot"]["password"],
                            "url": self.cfg["hotspot"]["upload_url"]},
            }

    # ---------- coins (called by the coin reader thread, never by a phone) ----------
    def coin(self, value):
        with self.lock:
            self.paid += value
            self._save_paid()
            self._touch()
            self.ledger("coin", value=value, paid=self.paid)
        log.info("coin +%d, paid=%d", value, self.paid)

    # ---------- print from phone ----------
    def phone_start(self):
        with self.lock:
            self._require("idle")
            self._set("phone_wait")

    def accepting_upload(self):
        return self.screen == "phone_wait"

    def upload_received(self, path: Path, name: str):
        with self.lock:
            if self.screen != "phone_wait":
                shutil.rmtree(path.parent, ignore_errors=True)
                raise MachineError("Machine busy")
            self._set("converting")
        self._spawn(self._prepare, path, name)

    # ---------- print from USB ----------
    def usb_open(self):
        with self.lock:
            self._require("idle")
            self._set("usb")

    def usb_files(self):
        return usb.list_files(self.cfg)

    def usb_select(self, path):
        with self.lock:
            self._require("usb")
            try:
                src = usb.validate(self.cfg, path)
            except ValueError as e:
                raise MachineError(str(e))
            if src.stat().st_size > self.L["max_upload_mb"] * 1024 * 1024:
                raise MachineError(f"File is too big (max {self.L['max_upload_mb']} MB)")
            dest = self.new_job_dir() / ("input" + src.suffix.lower())
            self._set("converting")
        self._spawn(self._copy_then_prepare, src, dest)

    def _copy_then_prepare(self, src, dest):
        try:
            shutil.copyfile(src, dest)
        except OSError:
            shutil.rmtree(dest.parent, ignore_errors=True)
            with self.lock:
                if self.screen == "converting":
                    self._set("error", "Could not read the file from the flash drive")
            return
        self._prepare(dest, src.name)

    def _prepare(self, path: Path, name: str):
        try:
            pdf = docs.to_pdf(path, self.L["convert_timeout_s"])
            pages = docs.count_pages(pdf)
            if pages > self.L["max_pages"]:
                raise docs.DocError(f"Too many pages ({pages}). The limit is {self.L['max_pages']}.")
        except Exception as e:
            if not isinstance(e, docs.DocError):
                log.exception("prepare failed")
            shutil.rmtree(path.parent, ignore_errors=True)
            with self.lock:
                if self.screen == "converting":
                    self._set("error", str(e) if isinstance(e, docs.DocError) else "Could not read this file")
            return
        with self.lock:
            if self.screen != "converting":       # cancelled while converting
                shutil.rmtree(path.parent, ignore_errors=True)
                return
            self.job = {"dir": path.parent, "pdf": pdf, "name": name, "pages": pages,
                        "mode": None, "price": None, "action": "print"}
            self._set("choose")

    # ---------- copy / scan to USB ----------
    def scan_start(self, action):
        if action not in ("copy", "usb"):
            raise MachineError("Unknown action")
        with self.lock:
            self._require("idle")
            if action == "usb" and not usb.drives(self.cfg):
                raise MachineError("Plug in a flash drive first")
            self.scan_action, self.scans = action, []
            self.scan_dir = self.new_job_dir()
            self._set("scanning")
        self._spawn(self._scan_one)

    def scan_more(self):
        with self.lock:
            self._require("scan_review")
            if len(self.scans) >= self.L["max_pages"]:
                raise MachineError(f"Limit is {self.L['max_pages']} pages")
            self._set("scanning")
        self._spawn(self._scan_one)

    def _scan_one(self):
        with self.lock:
            out = self.scan_dir / f"page{len(self.scans) + 1:03}.png"
        try:
            self.scanner.scan(out)
        except Exception:
            log.exception("scan failed")
            with self.lock:
                if self.screen == "scanning":
                    if self.scans:
                        self._set("scan_review", "Scan failed. Try again.")
                    else:
                        self._clear()
                        self._set("error", "Scanner error. Please try again later.")
            return
        with self.lock:
            if self.screen != "scanning":         # cancelled
                return
            self.scans.append(out)
            self._set("scan_review")

    def scan_done(self):
        with self.lock:
            self._require("scan_review")
            if not self.scans:
                raise MachineError("Nothing scanned yet")
            pdf = docs.images_to_pdf(self.scans, self.scan_dir / "scan.pdf", self.scanner.dpi)
            n = len(self.scans)
            action = "print" if self.scan_action == "copy" else "usb"
            self.job = {"dir": self.scan_dir, "pdf": pdf, "name": f"{n} scanned page(s)",
                        "pages": n, "mode": None, "price": None, "action": action}
            if action == "usb":
                self.job["mode"] = "scan"
                self.job["price"] = calc_price(self.cfg["price"], "scan", n)
            self.scan_dir, self.scans, self.scan_action = None, [], None
            self._set("choose")

    # ---------- choose, pay, run ----------
    def set_mode(self, mode):
        if mode not in ("color", "bw"):
            raise MachineError("Unknown mode")
        with self.lock:
            self._require("choose")
            if self.job["action"] != "print":
                raise MachineError("Not available")
            self.job["mode"] = mode
            self.job["price"] = calc_price(self.cfg["price"], mode, self.job["pages"])
            self._touch()

    def confirm(self):
        with self.lock:
            self._require("choose")
            j = self.job
            if j["price"] is None:
                raise MachineError("Choose Colored or Black and white first")
            if self.paid < j["price"]:
                raise MachineError(f"Insert P{j['price'] - self.paid} more")
            if j["action"] == "print" and not self.printer_ok:
                raise MachineError(self.printer_msg or "Printer not ready")
            if j["action"] == "usb" and not usb.drives(self.cfg):
                raise MachineError("Flash drive not found")
            j["code"] = uuid.uuid4().hex[:6].upper()
            self.ledger("pay", code=j["code"], action=j["action"], mode=j["mode"], pages=j["pages"],
                        price=j["price"], paid=self.paid, forfeit=self.paid - j["price"])
            self.paid = 0
            self._save_paid()
            self._set("printing" if j["action"] == "print" else "saving")
        self._spawn(self._run, j)

    def _run(self, j):
        try:
            if j["action"] == "print":
                self.printer.print(j["pdf"], j["mode"], j["pages"])
                msg = "Done! Collect your pages."
            else:
                name = usb.save(self.cfg, j["pdf"])
                msg = f"Saved to your flash drive as {name}. You can remove it now."
            self.ledger("done", code=j["code"])
            with self.lock:
                self._set("done", msg)
        except Exception as e:
            log.exception("job %s failed", j["code"])
            self.ledger("failed", code=j["code"], reason=str(e))
            with self.lock:
                self._set("error", f"{e}. Show this code to the operator: {j['code']}")
        finally:
            shutil.rmtree(j["dir"], ignore_errors=True)    # file deleted after printing
            with self.lock:
                if self.job is j:
                    self.job = None

    def cancel(self):
        with self.lock:
            if self.screen in ("printing", "saving"):
                raise MachineError("Can't cancel while printing")
            if self.screen == "out_of_service":
                return
            self._clear()
            self._set("idle")

    # ---------- background: timeouts + printer health ----------
    def _loop(self):
        while True:
            time.sleep(1)
            try:
                self._tick()
            except Exception:
                log.exception("tick failed")

    def _tick(self):
        now = time.monotonic()
        if now - self._last_check > 10 and self.screen in ("idle", "out_of_service"):
            self._last_check = now
            ok, msg = self.printer.status()            # subprocess, keep it outside the lock
            with self.lock:
                if ok != self.printer_ok:
                    self.ledger("printer", ok=ok, msg=msg)
                self.printer_ok, self.printer_msg = ok, msg
                if not ok and self.screen == "idle":
                    self._set("out_of_service", msg)
                elif ok and self.screen == "out_of_service":
                    self._set("idle")
        with self.lock:
            age, s = now - self.since, self.screen
            if s == "phone_wait" and age > self.L["phone_wait_s"]:
                self._set("idle")
            elif s in ("usb", "choose", "scan_review") and age > self.L["job_idle_s"]:
                self._clear()
                self._set("idle")
            elif s == "done" and age > self.L["done_screen_s"]:
                self._set("idle")
            elif s == "error" and age > self.L["error_screen_s"]:
                self._clear()
                self._set("idle")
