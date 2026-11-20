# kiosk-print

Coin-operated print / copy / scan kiosk for a Raspberry Pi 4.
One customer at a time. Python + FastAPI backend, plain HTML kiosk UI.

## Layout

```
config.toml              dev config (mock hardware). Prices live here.
deploy/config.pi.toml    Pi config (real hardware)
app/
  main.py                starts the kiosk server (127.0.0.1:8001)
  phone.py               phone sessions: random hotspot + port + token, two QR codes
  machine.py             THE logic: screens, paid amount, timeouts, ledger
  pricing.py             pesos * ceil(pages / pages_per_block)
  docs.py                Word/PPT -> PDF (LibreOffice), page count (pypdf)
  usb.py                 list flash drive files, save scans
  kiosk.py               touchscreen API (local only) + QR codes + dev coin route
  upload.py              upload page per session (the only thing phones can reach)
  hw/mock.py             FilePrinter (PDFs into ./out), MockScanner
  hw/real.py             GpioCoinReader, CupsPrinter (lp/lpstat), SaneScanner (scanimage)
web/kiosk/               800x480 UI (index.html, kiosk.js, kiosk.css)
web/upload/index.html    phone upload page
deploy/                  systemd unit, hotspot setup (+ polkit rule), USB gadget, kiosk autostart
tests/                   pytest: pricing + full flows with mock hardware
var/                     state.json (paid amount) + ledger.log   (created at runtime)
out/                     mock printer output                     (created at runtime)
fake_usb/                acts as a plugged-in flash drive in dev
```

## Run on Arch (dev)

```sh
sudo pacman -S --needed python libreoffice-fresh poppler
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m app.main
```

## Run on Pi OS Lite

How this machine was set up: a Pi 4 with no screen yet, powered and reached over **one USB-C cable**
from the PC (USB gadget mode), so the Pi's only Wi-Fi radio stays free for the phone hotspot.
Do the steps in order and check each one before the next.

### 1. Flash
Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager: user `kiosk`, SSH on, a Wi-Fi network you
can use for setup (e.g. a laptop hotspot). The Pi needs that Wi-Fi only for internet (`apt`, `git`).

### 2. USB link (SSH over the USB-C cable)
Why: a Pi 4 has one Wi-Fi radio. During a phone session it becomes the hotspot and the Pi leaves any
other Wi-Fi, so SSH over Wi-Fi would drop. The USB link stays up. Power: the PC's USB-C port powers
the Pi; check `vcgencmd get_throttled` says `throttled=0x0` (a PC USB-A port may be too weak).
Once the SSD and screen are added the PC can't power it: use a USB-C power/data splitter.

Do this over the setup Wi-Fi SSH.

`/boot/firmware/config.txt`, last lines:
```ini
[all]
dtoverlay=dwc2,dr_mode=peripheral
```

`/boot/firmware/cmdline.txt` stays **one line**; add `modules-load=dwc2` after `rootwait`, e.g.:
```
console=serial0,115200 console=tty1 root=PARTUUID=xxxxxxxx-02 rootfstype=ext4 fsck.repair=yes rootwait modules-load=dwc2 cfg80211.ieee80211_regdom=PH
```
(Not `g_ether`: Windows loads it as a COM port. The script below makes an RNDIS gadget that Windows
and Linux both recognise without driver picking.)

Gadget script + boot service:
```sh
cd ~/print-kiosk          # after step 4 (clone); or copy the two files over first
sudo cp deploy/usb-gadget/usb-gadget.sh /usr/local/sbin/
sudo chmod +x /usr/local/sbin/usb-gadget.sh
sudo cp deploy/usb-gadget/usb-gadget.service /etc/systemd/system/
sudo systemctl enable usb-gadget
```

Fixed address on the Pi side (no gateway: the link is for SSH only):
```sh
sudo nmcli con add type ethernet ifname usb0 con-name usb-gadget \
  ipv4.method manual ipv4.addresses 10.55.0.1/24 ipv6.method disabled
```
Run it once. A second run makes a duplicate profile: remove it with `sudo nmcli con delete <uuid>`.

Reboot, plug the Pi's **USB-C** port into the PC with a data cable.

**Arch PC** (NetworkManager). The interface shows up as e.g. `enp0s20f0u7` (`ip -br link`, MAC `02:22:82:ff:ff:02`):
```sh
sudo nmcli con add type ethernet ifname enp0s20f0u7 con-name pi-usb \
  ipv4.method manual ipv4.addresses 10.55.0.2/24 ipv4.gateway "" ipv6.method disabled
sudo nmcli con up pi-usb
ip -br addr show enp0s20f0u7      # must show ONLY 10.55.0.2/24
ping -c 3 10.55.0.1               # ~0.2-1 ms. ~0.03 ms means the PC is pinging itself (see below)
```
Don't use `ipv4.method shared` on the PC side: it gives the PC extra addresses (`10.42.x`) that
clash with the Pi's hotspot.

**Windows PC**: the Pi appears as "Remote NDIS Compatible Device" (`Get-NetAdapter`). As Administrator:
`New-NetIPAddress -InterfaceAlias "Ethernet 3" -IPAddress 10.55.0.2 -PrefixLength 24` (use the adapter's name).

SSH key (on the PC, not the Pi; press Enter at every `ssh-keygen` question):
```sh
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519
ssh-copy-id kiosk@10.55.0.1
ssh kiosk@10.55.0.1 hostname      # prints "kiosk", no password
```

### 3. Packages
```sh
sudo apt update && sudo apt install -y git python3-venv python3-gpiozero poppler-utils \
  libreoffice-writer-nogui libreoffice-impress-nogui fonts-crosextra-carlito \
  fonts-crosextra-caladea fonts-liberation cups printer-driver-cups-pdf \
  sane-utils sane-airscan udiskie curl
sudo usermod -aG lpadmin,scanner kiosk
which lpstat && systemctl is-active cups       # CUPS must be there, or the kiosk shows "CUPS not responding"
```
`printer-driver-cups-pdf` adds a virtual printer that saves PDFs: use it until the real printer is set up
(`lpstat -p` shows its name -> `[printer] cups_name` in `deploy/config.pi.toml`).

### 4. Code
```sh
git clone https://github.com/BoragSkibidi/print-kiosk ~/print-kiosk && cd ~/print-kiosk
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import gpiozero; print('gpiozero ok')"
```
`--system-site-packages` lets the venv use the apt `gpiozero` (coin slot). If the venv was made without
it, set `include-system-site-packages = true` in `.venv/pyvenv.cfg`.

### 5. Hotspot
```sh
sudo raspi-config nonint do_wifi_country PH
sudo ./deploy/hotspot.sh wlan0 kiosk
nmcli con up kiosk-hotspot        # WITHOUT sudo: tests the polkit rule. Phone sees "KioskPrint-SETUP"
nmcli con down kiosk-hotspot
```
"Not authorized to control networking" = the polkit rule didn't load. Check
`journalctl -u polkit -n 15` for a `SyntaxError`, fix `/etc/polkit-1/rules.d/50-kiosk-hotspot.rules`,
then `sudo systemctl restart polkit`.

### 6. Backend service (real hardware)
The app reads `config.toml` (simulated hardware) unless `KIOSK_CONFIG` says otherwise. Running
`.venv/bin/python -m app.main` by hand therefore always uses the mocks; the service uses
`deploy/config.pi.toml` (`coin = "gpio"`, `printer = "cups"`, `scanner = "sane"`, `hotspot = "nmcli"`).
```sh
sudo cp deploy/kiosk-print.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kiosk-print
journalctl -u kiosk-print -n 15       # "hardware: {'coin': 'gpio', 'printer': 'cups', ...}"
```
Stop any copy you started by hand first (it holds port 8001).
To test some parts for real and fake the rest, copy `deploy/config.pi.toml` to `deploy/config.test.toml`,
change `[hardware]`, and point `KIOSK_CONFIG` in the service at it.

### 7. Use it from the PC
```sh
ssh -L 8001:127.0.0.1:8001 kiosk@10.55.0.1     # then open http://127.0.0.1:8001 (800x480)
journalctl -fu kiosk-print                      # on the Pi: live log
```
Edit the code with your own editor by mounting the Pi's folder on the PC:
```sh
sudo pacman -S --needed sshfs
mkdir -p ~/pi && sshfs kiosk@10.55.0.1:print-kiosk ~/pi     # unmount: fusermount3 -u ~/pi
```
Open `~/pi` in nvim / any IDE. Run git, tests and the service on the Pi (over SSH), not in `~/pi`.
After editing Python: `sudo systemctl restart kiosk-print`. HTML / JS / CSS: refresh the browser.
Remount after a PC reboot or unplugging the Pi. VS Code can use Remote-SSH instead.

### Troubleshooting (all seen on this machine)
| Symptom | Cause / fix |
|---|---|
| Windows shows the Pi as **USB Serial Device (COMx)** | `g_ether` in `cmdline.txt`. Use the RNDIS gadget above, uninstall the COM device, reboot the Pi. |
| Ping replies in ~0.03 ms, SSH reaches the PC itself | PC has `10.55.0.1` too (old shared profile). `ip -br addr \| grep 10.55`; set the PC profile to manual `10.55.0.2/24` only. |
| `ModuleNotFoundError: gpiozero` in the service log | venv without system packages, see step 4. |
| Kiosk says **CUPS not responding** / out of service | CUPS not installed or not running (`which lpstat`), see step 3. Printer missing -> "Printer not found". |
| Phone joins the Wi-Fi but the page won't load | Turn off mobile data on the phone, stay on the kiosk Wi-Fi. If an old setup installed `/etc/NetworkManager/dnsmasq-shared.d/kiosk-captive.conf`, re-run `hotspot.sh` (it removes it). |
| No Wi-Fi on the phone, log says `mock hotspot up` | App started by hand with `config.toml`. Use the service (step 6). |
| `Not authorized to control networking` | polkit rule broken, see step 5. |
| `ls ~/pi` gives an `--icons` error | `ls` is aliased to eza; use `command ls ~/pi`. |

### Dev run on the Pi (simulated hardware)
```sh
sudo systemctl stop kiosk-print
.venv/bin/python -m pytest -q
.venv/bin/python -m app.main            # config.toml: mock coin / printer / scanner / hotspot
```
Mock coins: keys **1**, **5**, **0** (=P10) on the kiosk page, or
`curl -XPOST localhost:8001/api/dev/coin -H 'content-type: application/json' -d '{"value":5}'`.
Printed jobs land in `./out/` (`touch out/JAM` / `out/OFFLINE` to simulate problems). Money log: `var/ledger.log`.

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

## Finishing the machine (screen, kiosk mode)

After "Run on Pi OS Lite" works:

1. RAM folder for uploads (add to `/etc/fstab`, then reboot):
   `tmpfs /tmp tmpfs defaults,noatime,nosuid,size=512m 0 0`
2. Kiosk browser: `sudo apt install -y cage chromium`, `sudo raspi-config nonint do_boot_behaviour B2`,
   append `deploy/kiosk/bash_profile` to `~/.bash_profile`,
   `sudo cp deploy/kiosk/50-udiskie.rules /etc/polkit-1/rules.d/`, reboot.
3. Screen: follow the seller's instructions for the DSI screen (`/boot/firmware/config.txt` overlay if needed).
4. Real printer: add it in CUPS, put its `lpstat -p` name in `[printer] cups_name`. Scanner: `scanimage -L`.

## Hardening (do last, once everything works)

- Hardware watchdog: set `RuntimeWatchdogSec=15` in `/etc/systemd/system.conf`.
- Auto updates off: `sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer`.
- CUPS keeps no copies: in `/etc/cups/cupsd.conf` set `PreserveJobFiles No` and `PreserveJobHistory No`.
- Read-only root (protects the SD card from power cuts): `sudo raspi-config` -> Performance -> Overlay File System.
  Keep `var/` on a writable partition or the paid amount and ledger won't survive reboots.

## Rules built in

- Money: `paid` is saved to `var/state.json` after every coin. On payment it resets to 0; extra coins are forfeited (no change) and logged as `forfeit`.
- A failed print shows a 6-character code; find it in `ledger.log` to refund by hand.
- Phone sessions: tapping "Print from phone" brings the hotspot up with a new random SSID + password and
  starts the upload page on a random port under a random token. The screen shows two QR codes:
  1. join the Wi-Fi, 2. open the upload page. The first phone to open it owns the session; others get "another phone is
  using this machine". On upload, cancel or timeout the servers stop and the hotspot goes down.
- No captive portal: phones open sign-in pop-ups inconsistently and those pop-ups can't pick files.
  Page won't load on a phone? Mobile data off (phones send traffic out mobile data on Wi-Fi with no internet).
- Copies: pages x copies counts toward `max_pages` and the price.
- Files live in `work_dir` (RAM), are deleted after printing/failure/cancel/timeout, and the folder is wiped on start.
- The kiosk/payment API listens on 127.0.0.1 only, so phones can't add coins or press Print.

## TODO / open

- Scan to USB costs the same as B&W printing (override with `[price] scan`). Scanning and rescanning are free; the scan screen shows a preview of each page and the running price.
- CUPS "completed" means data sent, not paper out. Test on the real printer.

## Tools Used For Development

- Arch Linux 	- by BoragSkibidi
- Neovim 		- by BoragSkibidi
- Windows 11 - by NatsuShii
- VSCode 		- by NatsuShii