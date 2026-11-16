# kiosk-print

Coin-operated print / copy / scan kiosk for a Raspberry Pi 4.
One customer at a time. Python + FastAPI backend, plain HTML kiosk UI.

## Layout

```
config.toml              dev config (mock hardware). Prices live here.
deploy/config.pi.toml    Pi config (real hardware)
app/
  main.py                starts both servers (kiosk 127.0.0.1:8001, upload 0.0.0.0:8000)
  machine.py             THE logic: screens, paid amount, timeouts, ledger
  pricing.py             pesos * ceil(pages / pages_per_block)
  docs.py                Word/PPT -> PDF (LibreOffice), page count (pypdf)
  usb.py                 list flash drive files, save scans
  kiosk.py               touchscreen API (local only) + QR codes + dev coin route
  upload.py              phone upload page API (the only thing phones can reach)
  hw/mock.py             FilePrinter (PDFs into ./out), MockScanner
  hw/real.py             GpioCoinReader, CupsPrinter (lp/lpstat), SaneScanner (scanimage)
web/kiosk/               800x480 UI (index.html, kiosk.js, kiosk.css)
web/upload/index.html    phone upload page
deploy/                  systemd unit, hotspot script, kiosk autostart, polkit rule
tests/                   pytest: pricing + full flows with mock hardware
var/                     state.json (paid amount) + ledger.log   (created at runtime)
out/                     mock printer output                     (created at runtime)
fake_usb/                acts as a plugged-in flash drive in dev
```

## Run on Arch (dev)

```sh
sudo pacman -S --needed python libreoffice-fresh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 21 tests
.venv/bin/python -m app.main
```

- Kiosk: http://127.0.0.1:8001 (open with a window of 800x480, e.g. devtools device mode)
- Phone page: http://127.0.0.1:8000 (or `http://<your-LAN-IP>:8000` from a real phone; also set `upload_url`)
- Insert coins (mock): press **1**, **5** or **0** (=P10) on the kiosk page, or
  `curl -XPOST localhost:8001/api/dev/coin -H 'content-type: application/json' -d '{"value":5}'`
- Printed jobs land in `./out/`. Simulate problems: `touch out/JAM` (next print fails),
  `touch out/OFFLINE` (out of service within 10 s). `rm` them to recover.
- Money log: `cat var/ledger.log` (one JSON line per coin, payment, success, failure)

## Switch to real hardware one part at a time

Each part is independent in `[hardware]`, so you can test the printer before the coin acceptor arrives.

**Printer (`printer = "cups"`)**: works on Arch too.
```sh
sudo pacman -S cups cups-pdf && sudo systemctl enable --now cups
lpstat -p                         # find the printer name -> [printer] cups_name
lpoptions -p NAME -l              # check which color option names it accepts
```
Driverless printers: `lpadmin -p NAME -E -v ipp://PRINTER-IP/ipp/print -m everywhere`.
Check that `color_options` / `bw_options` really change the output, and pull paper / open the door
to see what `lpstat -l -p NAME` reports (the keywords are in `CupsPrinter.REASONS`).

**Scanner (`scanner = "sane"`)**: `sudo pacman -S sane sane-airscan`, then `scanimage -L`.
If `--mode=Color` errors, run `scanimage -A` to see your scanner's option names.

**Coin acceptor (`coin = "gpio"`, Pi only)**
- Program the acceptor so P1 = 1 pulse, P5 = 5 pulses, P10 = 10 pulses (`pesos_per_pulse = 1`).
- Wiring (optocoupler PC817):
  ```
  12V side:  acceptor COIN signal -> 1k resistor -> PC817 pin 1 (anode)
             PC817 pin 2 (cathode) -> acceptor GND
             (if the signal is open-collector: 12V -> 1k -> pin 1, pin 2 -> COIN signal)
  Pi side:   PC817 pin 4 (collector) -> GPIO17 (pin 11)
             PC817 pin 3 (emitter)   -> Pi GND (pin 9)
  ```
  Do not join the two grounds. Check the pulse voltage with a multimeter first.
- Watch it: `journalctl -u kiosk-print -f` prints `coin: N pulses -> PX`.
  If coins are miscounted, tune `pulse_gap_ms` / `debounce_ms`.

## Raspberry Pi setup

1. Flash **Raspberry Pi OS Lite 64-bit** with Raspberry Pi Imager: user `kiosk`, SSH on, your Wi-Fi.
2. Packages:
   ```sh
   sudo apt update && sudo apt install -y git python3-venv python3-gpiozero libreoffice-writer-nogui \
     libreoffice-impress-nogui fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation \
     cups sane-utils sane-airscan udiskie cage chromium curl
   sudo usermod -aG lpadmin,scanner kiosk
   ```
3. Code:
   ```sh
   git clone <your repo> ~/kiosk-print && cd ~/kiosk-print
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```
4. RAM folder for uploads (add to `/etc/fstab`, then reboot):
   `tmpfs /tmp tmpfs defaults,noatime,nosuid,size=512m 0 0`
5. Hotspot: `sudo raspi-config nonint do_wifi_country PH && sudo ./deploy/hotspot.sh KioskPrint 'yourpassword'`
   (set up SSH over **Ethernet** first; the hotspot takes over wlan0). Put the same ssid/password in `deploy/config.pi.toml`.
6. Backend service: `sudo cp deploy/kiosk-print.service /etc/systemd/system/ && sudo systemctl enable --now kiosk-print`
7. Kiosk: `sudo raspi-config nonint do_boot_behaviour B2`, `cat deploy/kiosk/bash_profile >> ~/.bash_profile`,
   `sudo cp deploy/kiosk/50-udiskie.rules /etc/polkit-1/rules.d/`, reboot.
8. Screen: follow the seller's instructions for the DSI screen (`/boot/firmware/config.txt` overlay if needed).

## Hardening (do last, once everything works)

- Hardware watchdog: set `RuntimeWatchdogSec=15` in `/etc/systemd/system.conf`.
- Auto updates off: `sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer`.
- CUPS keeps no copies: in `/etc/cups/cupsd.conf` set `PreserveJobFiles No` and `PreserveJobHistory No`.
- Read-only root (protects the SD card from power cuts): `sudo raspi-config` -> Performance -> Overlay File System.
  Keep `var/` on a writable partition or the paid amount and ledger won't survive reboots.

## Rules built in

- Money: `paid` is saved to `var/state.json` after every coin. On payment it resets to 0; extra coins are forfeited (no change) and logged as `forfeit`.
- A failed print shows a 6-character code; find it in `ledger.log` to refund by hand.
- Phone uploads only accepted after "Print from phone" is tapped; anyone else gets "Machine busy".
- Files live in `work_dir` (RAM), are deleted after printing/failure/cancel/timeout, and the folder is wiped on start.
- The kiosk/payment API listens on 127.0.0.1 only, so phones can't add coins or press Print.

## TODO / open

- Scan-to-USB price is a placeholder (`[price] scan`).
- CUPS "completed" means data sent, not paper out. Test on the real printer.
- Captive portal skipped: customers scan two QR codes (Wi-Fi, then the URL).
