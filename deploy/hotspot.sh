#!/usr/bin/env bash
# Turn the Pi's Wi-Fi into a hotspot at 10.42.0.1 (NetworkManager does DHCP).
# Usage: sudo ./deploy/hotspot.sh KioskPrint 'your-password' [wlan0]
# Set the Wi-Fi country first: sudo raspi-config nonint do_wifi_country PH
set -euo pipefail
SSID="${1:?ssid}"
PASS="${2:?password (8+ characters)}"
IFACE="${3:-wlan0}"

nmcli con delete kiosk-hotspot 2>/dev/null || true
nmcli con add type wifi ifname "$IFACE" con-name kiosk-hotspot autoconnect yes ssid "$SSID" \
  connection.autoconnect-priority 100 \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
nmcli con up kiosk-hotspot
echo "Hotspot '$SSID' up on $IFACE at 10.42.0.1. Put the same ssid/password in deploy/config.pi.toml."
