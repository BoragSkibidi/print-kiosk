"""Real hardware: coin acceptor on GPIO, printer via CUPS (lp/lpstat), scanner via SANE (scanimage).
CUPS and SANE also work on your Arch PC, so test them there first."""
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("kiosk-print.hw")


class GpioCoinReader:
    """Coin acceptor sends N pulses per coin. Group pulses separated by < pulse_gap_ms."""

    def __init__(self, c, on_coin):
        from gpiozero import Button          # only installed on the Pi
        self.on_coin = on_coin
        self.per = c["pesos_per_pulse"]
        self.gap = c["pulse_gap_ms"] / 1000
        self.debounce = c["debounce_ms"] / 1000
        self.pulses, self.last, self.timer = 0, 0.0, None
        self.lock = threading.Lock()
        # active_low: pin pulled up, pulse pulls it to GND (optocoupler output)
        self.pin = Button(c["gpio_pin"], pull_up=c["active_low"], bounce_time=None)
        self.pin.when_pressed = self._pulse
        log.info("coin reader on GPIO%d", c["gpio_pin"])

    def _pulse(self):
        now = time.monotonic()
        with self.lock:
            if now - self.last < self.debounce:
                return
            self.last = now
            self.pulses += 1
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.gap, self._finish)
            self.timer.daemon = True
            self.timer.start()

    def _finish(self):
        with self.lock:
            n, self.pulses, self.timer = self.pulses, 0, None
        log.info("coin: %d pulses -> P%d", n, n * self.per)
        if n:
            self.on_coin(n * self.per)


def _run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class CupsPrinter:
    # printer-state-reasons keywords -> message shown on the kiosk
    REASONS = {"media-jam": "Paper jam", "media-empty": "Out of paper",
               "media-needed": "Out of paper", "marker-supply-empty": "Out of ink",
               "door-open": "Printer door open", "offline": "Printer offline"}

    def __init__(self, c):
        self.c = c
        self.name = c["cups_name"]

    def status(self):
        try:
            r = _run(["lpstat", "-l", "-p", self.name])
        except (OSError, subprocess.TimeoutExpired):
            return False, "CUPS not responding"
        if r.returncode != 0:
            return False, "Printer not found"
        out = r.stdout.lower()
        for key, msg in self.REASONS.items():
            if key in out:
                return False, msg
        if "disabled" in out:
            return False, "Printer stopped"
        return True, ""

    def print(self, pdf: Path, mode, pages):
        opts = list(self.c["extra_options"])
        opts += self.c["color_options"] if mode == "color" else self.c["bw_options"]
        cmd = ["lp", "-d", self.name]
        for o in opts:
            cmd += ["-o", o]
        r = _run(cmd + [str(pdf)])
        m = re.search(r"request id is (\S+)", r.stdout)
        if r.returncode != 0 or not m:
            raise RuntimeError("Printer refused the job")
        job = m.group(1)
        log.info("CUPS job %s (%s, %d pages)", job, mode, pages)

        # NOTE: CUPS "completed" means data was sent, not that paper came out.
        # Good enough for the prototype; check pages-completed via IPP later if needed.
        deadline = time.monotonic() + self.c["job_timeout_s"]
        while True:
            time.sleep(2)
            pending = _run(["lpstat", "-W", "not-completed", "-o", self.name]).stdout
            if job not in pending:
                break
            ok, msg = self.status()
            if not ok:
                raise RuntimeError(msg)
            if time.monotonic() > deadline:
                raise RuntimeError("Printing took too long")
        done = _run(["lpstat", "-W", "completed", "-o", self.name]).stdout
        if job not in done:
            raise RuntimeError("Print job was cancelled")


class SaneScanner:
    def __init__(self, c):
        self.c = c
        self.dpi = c["resolution"]

    def scan(self, out: Path):
        cmd = ["scanimage", "--format=png", f"--resolution={self.dpi}", "-o", str(out)]
        if self.c.get("device"):
            cmd[1:1] = ["-d", self.c["device"]]
        if self.c.get("mode"):
            cmd.append(f"--mode={self.c['mode']}")
        r = _run(cmd, timeout=180)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError("Scanner error: " + r.stderr.strip()[-200:])
        return out
