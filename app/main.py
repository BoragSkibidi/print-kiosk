"""Entry point: `python -m app.main`.
  kiosk -> 127.0.0.1:<kiosk_port>   (touchscreen, payment, controls), always running
  phone -> upload page on a random port, only during a phone session (see phone.py)
"""
import asyncio
import logging
import signal
import tempfile
from pathlib import Path

import uvicorn

from . import hw, kiosk, upload
from .config import load_config
from .machine import Machine
from .phone import PhoneManager, Server

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("kiosk-print")


async def serve(servers: Server):
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
    web = ROOT / "web"
    phone = PhoneManager(cfg)
    machine = Machine(cfg, printer, scanner, phone)
    phone.make_apps = lambda ses: upload.create(machine, cfg, web, ses)

    # Upload spooling goes to RAM too, never the SD card.
    spool = machine.work / "spool"
    spool.mkdir(exist_ok=True)
    tempfile.tempdir = str(spool)

    coin_reader = hw.start_coin_reader(cfg, machine.coin)  # noqa: F841 (keep a reference)

    s = cfg["server"]
    servers = [
        Server(
            uvicorn.Config(
                kiosk.create(machine, cfg, web),
                host="127.0.0.1",
                port=s["kiosk_port"],
                log_level="warning"
            )
        ),
    ]

    log.info("kiosk  http://127.0.0.1:%d", s["kiosk_port"])
    log.info("phone sessions: upload page on a random port %s", s["upload_port_range"])
    log.info("hardware: %s", cfg["hardware"])
    asyncio.run(serve(servers))


if __name__ == "__main__":
    main()