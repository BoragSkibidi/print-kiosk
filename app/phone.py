import contextlib
import logging
import secrets
import socket
import subprocess
import threading
import time
 
import uvicorn
 
log = logging.getLogger("kiosk-print.phone")
 
# No 0/o, 1/l/i: easy to type if the customer joins by hand.
PASSWORD_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"
 
 
class Server(uvicorn.Server):
    """uvicorn server that can run in a thread (we handle signals in main)."""
 
    def install_signal_handlers(self):          # older uvicorn
        pass
 
    @contextlib.contextmanager
    def capture_signals(self):                  # newer uvicorn
        yield
 
 
class Session:
    def __init__(self, cfg, port):
        h, s = cfg["hotspot"], cfg["server"]
        self.ssid = f"{h['ssid_prefix']}-{secrets.token_hex(2).upper()}"
        self.password = "".join(secrets.choice(PASSWORD_CHARS) for _ in range(10))
        self.token = secrets.token_urlsafe(9)
        self.port = port
        self.host = h["host"]
        self.captive_port = s["captive_port"]
        self.state = "starting"     # starting | ready | failed
        self.owner = None           # IP of the phone that opened the upload page first
        self.result = "expired"     # how it ended: ready | failed | expired (phone page shows it)
        self.message = ""
 
    @property
    def upload_url(self):
        return f"http://{self.host}:{self.port}/{self.token}/"
 
    @property
    def portal_url(self):           # what a customer can type if the page doesn't pop up
        port = "" if self.captive_port == 80 else f":{self.captive_port}"
        return f"http://{self.host}{port}/"
 
 
class MockHotspot:
    def up(self, ssid, password):
        log.info("mock hotspot up: %s / %s", ssid, password)
 
    def down(self):
        log.info("mock hotspot down")
 
 
class NmHotspot:
    """Changes SSID + password of the existing NetworkManager hotspot connection."""
 
    def __init__(self, cfg):
        self.con = cfg["hotspot"]["connection"]
 
    def _nmcli(self, *args, timeout=45):
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"nmcli {' '.join(args[:2])}: {r.stderr.strip()[-200:]}")
 
    def up(self, ssid, password):
        self._nmcli("con", "modify", self.con, "802-11-wireless.ssid", ssid, "wifi-sec.psk", password)
        self._nmcli("--wait", "30", "con", "up", self.con)
 
    def down(self):
        with contextlib.suppress(Exception):
            self._nmcli("con", "down", self.con, timeout=20)
 
 
def free_port(lo, hi):
    for _ in range(100):
        port = lo + secrets.randbelow(hi - lo)
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")
 
 
class PhoneManager:
    """Starts and stops sessions. Slow work (Wi-Fi, servers) runs in background threads,
    one at a time; a session that is no longer current is simply skipped."""
 
    END_GRACE_S = 6      # keep Wi-Fi up a moment so the phone page sees the result (it polls every 1 s)
 
    def __init__(self, cfg, hotspot=None, make_apps=None, serve=True):
        self.cfg = cfg
        if hotspot is None:
            kind = cfg["hardware"].get("hotspot", "mock")
            if kind not in ("mock", "nmcli"):
                raise ValueError("hardware.hotspot must be 'mock' or 'nmcli'")
            hotspot = NmHotspot(cfg) if kind == "nmcli" else MockHotspot()
        self.hotspot = hotspot
        self.make_apps = make_apps    # session -> (captive_app, upload_app); set by main
        self.serve = serve            # False in tests: no real sockets
        self.current = None
        self.servers = []
        self.op_lock = threading.Lock()
        threading.Thread(target=self._boot_down, daemon=True).start()
 
    def _boot_down(self):
        with self.op_lock:
            self.hotspot.down()       # never left open from a previous run
 
    def new_session(self):
        lo, hi = self.cfg["server"]["upload_port_range"]
        self.current = Session(self.cfg, free_port(lo, hi))
        return self.current
 
    def start(self, session, on_ready, on_fail):
        threading.Thread(target=self._start, args=(session, on_ready, on_fail), daemon=True).start()
 
    def _start(self, session, on_ready, on_fail):
        with self.op_lock:
            if self.current is not session:
                return
            self._stop_servers()
            try:
                self.hotspot.up(session.ssid, session.password)
                if self.serve and self.make_apps:
                    captive, upload = self.make_apps(session)
                    self.servers = [self._run(captive, session.captive_port),
                                    self._run(upload, session.port)]
            except Exception:
                log.exception("phone session failed to start")
                self._stop_servers()
                self.hotspot.down()
                session.state = "failed"
                on_fail(session)
                return
            log.info("phone session ready: wifi %s, upload %s", session.ssid, session.upload_url)
        on_ready(session)
 
    def end(self, session):
        if self.current is session:
            self.current = None
        threading.Thread(target=self._end, args=(session,), daemon=True).start()
 
    def _end(self, session):
        time.sleep(self.END_GRACE_S if self.serve else 0)
        with self.op_lock:
            if self.current is not None:          # a newer session already took over
                return
            self._stop_servers()
            self.hotspot.down()
 
    # ---------- uvicorn servers in threads ----------
    def _run(self, app, port):
        srv = Server(uvicorn.Config(app, host=self.cfg["server"]["public_host"], port=port,
                                    log_level="warning"))
        t = threading.Thread(target=srv.run, daemon=True)
        t.start()
        end = time.monotonic() + 10
        while not srv.started:
            if not t.is_alive() or time.monotonic() > end:
                raise RuntimeError(f"server on port {port} did not start")
            time.sleep(0.05)
        srv.thread = t
        return srv
 
    def _stop_servers(self):
        for srv in self.servers:
            srv.should_exit = True
        for srv in self.servers:
            srv.thread.join(5)
        self.servers = []