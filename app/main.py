"""Entry point: `python -m app.main`. Runs two web servers in one process:
  kiosk  -> 127.0.0.1:<kiosk_port>        (touchscreen, payment, controls)
  public -> <public_host>:<public_port>   (phone upload page only)
"""
import asyncio
import contextlib
import logging
import signal
import tempfile
from pathlib import Path

import uvicorn

from . import hw, kiosk, upload
from .config import load_config
from .machine import Machine

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kiosk-print")


class Server(uvicorn.Server):
    # We run two servers, so we handle Ctrl+C / SIGTERM ourselves.
    def install_signal_handlers(self):          # older uvicorn
        pass

    @contextlib.contextmanager
    def capture_signals(self):                  # newer uvicorn
        yield


async def serve(servers):
    loop = asyncio.get_running_loop()

    def stop():
        for s in servers:
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    printer, scanner = hw.build(cfg)
    machine = Machine(cfg, printer, scanner)

    # Upload spooling goes to RAM too, never the SD card.
    spool = machine.work / "spool"
    spool.mkdir(exist_ok=True)
    tempfile.tempdir = str(spool)

    coin_reader = hw.start_coin_reader(cfg, machine.coin)  # noqa: F841 (keep a reference)

    s, web = cfg["server"], ROOT / "web"
    servers = [
        Server(uvicorn.Config(kiosk.create(machine, cfg, web), host="127.0.0.1",
                              port=s["kiosk_port"], log_level="warning")),
        Server(uvicorn.Config(upload.create(machine, cfg, web), host=s["public_host"],
                              port=s["public_port"], log_level="warning")),
    ]
    log.info("kiosk  http://127.0.0.1:%d", s["kiosk_port"])
    log.info("upload http://%s:%d", s["public_host"], s["public_port"])
    log.info("hardware: %s", cfg["hardware"])
    asyncio.run(serve(servers))


if __name__ == "__main__":
    main()
