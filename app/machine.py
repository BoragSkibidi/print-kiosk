"""The machine: one customer at a time, so this is one state variable, not a job list.

Scanning is free (rescan included); only printing / saving to USB costs.

Phone uploads run in short sessions (random Wi-Fi + port + token), see phone.py.
Copy / scan to USB open on an empty preview; nothing is scanned until "Scan" is tapped.

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
from .phone import PhoneManager
from .pricing import price as calc_price

log = logging.getLogger("kiosk-print.machine")


class MachineError(Exception):
    """Shown to the customer as-is."""


class Machine:
    def __init__(self, cfg, printer, scanner, phone=None):
        self.cfg = cfg
        self.L = cfg["limits"]
        self.printer = printer
        self.scanner = scanner
        self.lock = threading.RLock()
        self.render_slots = threading.Semaphore(2)   # max PDF page renders at once (Pi CPU)

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
        self.rescan_index = None   # page being replaced by a rescan (0-based)
        self.progress = None       # 0-100 while the scanner reports progress
        self.phone = phone or PhoneManager(cfg, serve=False)
        self.session = None        # phone session while on phone_wait
        self.printer_ok, self.printer_msg = True, ""
        self._last_check = 0.0

        self.ledger("boot", paid=self.paid)
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- persistence ----------
    def _load_paid(self) -> int:
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
        # The phone session lives through "converting" so the phone page can follow its file;
        # it ends once the file is on screen (choose), fails (error) or is cancelled (idle).
        if self.session and screen not in ("phone_wait", "converting"):
            s = self.session
            s.result = {"choose": "ready", "error": "failed"}.get(screen, "expired")
            s.message = message
            self.phone.end(s)
            self.session = None
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
        self.scans, self.scan_action, self.rescan_index = [], None, None

    def _scan_quote(self):
        """What the scanned pages will cost, shown while scanning."""
        n = len(self.scans)
        if not n:
            return None
        if self.scan_action == "usb":
            return {"scan": calc_price(self.cfg["price"], "scan", n)}
        return {k: calc_price(self.cfg["price"], k, n) for k in ("color", "bw")}

    def scan_preview(self, index, full=False):
        """Scanned page `index` (0-based): small JPEG, or the full-size PNG for zooming."""
        with self.lock:
            if self.screen not in ("scan_review", "scanning") or not 0 <= index < len(self.scans):
                raise MachineError("No such page")
            png = self.scans[index]
            return png if full else png.with_suffix(".jpg")

    def job_preview(self, index, full=False):
        """Page `index` (0-based) of the loaded job's PDF as a JPEG, rendered on first request."""
        with self.lock:
            j = self.job
            if self.screen != "choose" or not j or not 0 <= index < j["pages"]:
                raise MachineError("No such page")
            pdf, out = j["pdf"], j["dir"] / "preview" / f"{'full' if full else 'thumb'}{index + 1:03}.jpg"
        if out.exists():
            return out
        with self.render_slots:                   # outside the lock: rendering takes a while
            if not out.exists():
                try:
                    docs.render_page(pdf, index, out, 1600 if full else 600)
                except docs.DocError as e:
                    raise MachineError(str(e))
        return out

    def _job_price(self, j):
        return calc_price(self.cfg["price"], j["mode"], j["pages"] * j["copies"])

    def _max_copies(self, j):
        return max(1, self.L["max_pages"] // j["pages"])

    def snapshot(self):
        with self.lock:
            j, ses = self.job, self.session
            price = j["price"] if j else None
            return {
                "screen": self.screen,
                "message": self.message,
                "paid": self.paid,
                "job": None if not j else {
                    **{k: j[k] for k in ("name", "pages", "mode", "price", "action", "copies")},
                    "max_copies": self._max_copies(j),
                    "id": j["dir"].name
                },                  # new id per job -> fresh previews
                "enough": price is not None and self.paid >= price,
                "scans": len(self.scans),
                "scan_pages": [p.stem for p in self.scans],   # changes on rescan -> new preview
                "scan_quote": self._scan_quote(),
                "rescan_index": self.rescan_index,
                "progress": self.progress,
                "scan_action": self.scan_action,
                "prices": self.cfg["price"],
                "usb_present": bool(usb.drives(self.cfg)),
                "mock_coins": self.cfg["hardware"]["coin"] == "mock",
                "phone": None if not ses else {
                    "state": ses.state, "ssid": ses.ssid, "password": ses.password,
                    "url": ses.upload_url, "connected": ses.owner is not None
                },
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
            self.session = self.phone.new_session()
            self.phone.start(self.session, self._phone_ready, self._phone_failed)

    def _phone_ready(self, session):
        with self.lock:
            if session is self.session:
                session.state = "ready"
                self._touch()             # the wait timer starts once the Wi-Fi is up

    def _phone_failed(self, session):
        with self.lock:
            if session is self.session and self.screen == "phone_wait":
                self._set("error", "Wi-Fi for phones could not start. Please try again.")

    def phone_claim(self, session, ip):
        """The first device to open the upload page owns the session. True if `ip` may use it."""
        with self.lock:
            if session.owner is None and session is self.session:
                session.owner = ip
                self._touch()
                log.info("phone %s connected", ip)
            return session.owner == ip

    def phone_stage(self, session):
        """What the phone page shows: waiting | processing | ready | failed | expired."""
        with self.lock:
            if session is self.session:
                return "waiting" if self.screen == "phone_wait" else "processing"
            return session.result

    def accepting_upload(self, session=None):
        return self.screen == "phone_wait" and (session is None or session is self.session)

    def upload_received(self, path: Path, name: str, session=None):
        with self.lock:
            if not self.accepting_upload(session):
                shutil.rmtree(path.parent, ignore_errors=True)
                raise MachineError("Machine busy")
            if self.session:
                self.session.message = ""         # new try: forget the last file's problem
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
            msg = str(e) if isinstance(e, docs.DocError) else "Could not read this file"
            with self.lock:
                if self.screen == "converting":
                    if self.session:
                        # Phone upload: keep the session (same Wi-Fi, same phone) so the
                        # customer can just pick another file.
                        self.session.message = msg
                        self._set("phone_wait", msg)
                    else:
                        self._set("error", msg)
            return
        with self.lock:
            if self.screen != "converting":       # cancelled while converting
                shutil.rmtree(path.parent, ignore_errors=True)
                return
            self.job = {"dir": path.parent, "pdf": pdf, "name": name, "pages": pages,
                        "mode": None, "price": None, "action": "print", "copies": 1}
            self._set("choose")

    # ---------- copy / scan to USB ----------
    def scan_start(self, action):
        if action not in ("copy", "usb"):
            raise MachineError("Unknown action")
        with self.lock:
            self._require("idle")
            if action == "usb" and not usb.drives(self.cfg):
                raise MachineError("Plug in a flash drive first")
            self.scan_action, self.scans, self.rescan_index = action, [], None
            self.scan_dir = self.new_job_dir()
            self._set("scan_review")          # empty preview; customer taps "Scan"

    def scan_more(self):
        """Scan a new page (also the very first one)."""
        with self.lock:
            self._require("scan_review")
            if len(self.scans) >= self.L["max_pages"]:
                raise MachineError(f"Limit is {self.L['max_pages']} pages")
            self._set("scanning")
        self._spawn(self._scan_one)

    def scan_rescan(self, index):
        """Scan page `index` (0-based) again and replace it. Free: only printing/saving costs."""
        with self.lock:
            self._require("scan_review")
            if not isinstance(index, int) or not 0 <= index < len(self.scans):
                raise MachineError("No such page")
            self.rescan_index = index
            self._set("scanning")
        self._spawn(self._scan_one)

    def _scan_one(self):
        with self.lock:
            scan_dir = self.scan_dir
            out = scan_dir / f"page_{uuid.uuid4().hex[:8]}.png"   # new name so previews refresh
            self.progress = 0

        def progress(pct):
            with self.lock:
                if self.scan_dir is scan_dir:
                    self.progress = max(0, min(100, int(pct)))

        try:
            self.scanner.scan(out, progress)
            self._make_preview(out)
        except Exception:
            log.exception("scan failed")
            out.unlink(missing_ok=True)
            with self.lock:
                self.rescan_index, self.progress = None, None
                if self.screen == "scanning":
                    self._set("scan_review", "Scan failed. Try again.")
            return
        with self.lock:
            self.progress = None
            if self.screen != "scanning" or self.scan_dir is not scan_dir:   # cancelled
                return
            i, self.rescan_index = self.rescan_index, None
            if i is None:
                self.scans.append(out)
                self._set("scan_review")
            else:
                old, self.scans[i] = self.scans[i], out
                old.unlink(missing_ok=True)
                old.with_suffix(".jpg").unlink(missing_ok=True)
                self._set("scan_review", f"Page {i + 1} scanned again.")

    @staticmethod
    def _make_preview(png: Path):
        from PIL import Image
        with Image.open(png) as img:
            img = img.convert("RGB")
            img.thumbnail((600, 600))
            img.save(png.with_suffix(".jpg"), quality=80)

    def scan_done(self):
        with self.lock:
            self._require("scan_review")
            if not self.scans:
                raise MachineError("Nothing scanned yet")
            pdf = docs.images_to_pdf(self.scans, self.scan_dir / "scan.pdf", self.scanner.dpi)
            n = len(self.scans)
            action = "print" if self.scan_action == "copy" else "usb"
            self.job = {"dir": self.scan_dir, "pdf": pdf, "name": f"{n} scanned page(s)",
                        "pages": n, "mode": None, "price": None, "action": action, "copies": 1}
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
            self.job["price"] = self._job_price(self.job)
            self._touch()

    def set_copies(self, copies):
        """Copies count toward the page limit and the price (pages x copies)."""
        with self.lock:
            self._require("choose")
            j = self.job
            if j["action"] != "print":
                raise MachineError("Not available")
            if not isinstance(copies, int) or isinstance(copies, bool) or copies < 1:
                raise MachineError("At least 1 copy")
            if copies > self._max_copies(j):
                raise MachineError(f"Limit is {self.L['max_pages']} pages in total")
            j["copies"] = copies
            if j["mode"]:
                j["price"] = self._job_price(j)
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
                        copies=j["copies"], price=j["price"], paid=self.paid, forfeit=self.paid - j["price"])
            self.paid = 0
            self._save_paid()
            self._set("printing" if j["action"] == "print" else "saving")
        self._spawn(self._run, j)

    def _run(self, j):
        try:
            if j["action"] == "print":
                self.printer.print(j["pdf"], j["mode"], j["pages"], j["copies"])
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