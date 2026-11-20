#!/usr/bin/env bash
# One-time hotspot setup on the Pi. The app changes the SSID and password itself
# for every phone session (random each time) and only turns the hotspot on while
# a customer is on the "Print from phone" screen.
#
# Usage: sudo ./deploy/hotspot.sh [wlan0] [service-user]
# Set the Wi-Fi country first: sudo raspi-config nonint do_wifi_country PH
set -euo pipefail
IFACE="${1:-wlan0}"
APP_USER="${2:-kiosk}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. NetworkManager AP connection at 10.42.0.1. Placeholder SSID/password: the app replaces them.
nmcli con delete kiosk-hotspot 2>/dev/null || true
nmcli con add type wifi ifname "$IFACE" con-name kiosk-hotspot autoconnect no ssid KioskPrint-SETUP \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$(head -c 12 /dev/urandom | base64)"

# 2. Captive portal DNS: every name resolves to the Pi, so the phone's
#    "am I online?" check lands on our portal and the upload page pops up.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
install -m 644 "$HERE/dnsmasq-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/kiosk-captive.conf

# 3. Let the app's user switch the hotspot on/off without root.
sed "s/\"kiosk\"/\"$APP_USER\"/" "$HERE/50-kiosk-hotspot.rules" > /etc/polkit-1/rules.d/50-kiosk-hotspot.rules
chmod 644 /etc/polkit-1/rules.d/50-kiosk-hotspot.rules
systemctl restart polkit NetworkManager

echo "Hotspot connection 'kiosk-hotspot' ready on $IFACE (off until a phone session starts)."
echo "Test as $APP_USER:  sudo -u $APP_USER nmcli con up kiosk-hotspot && sudo -u $APP_USER nmcli con down kiosk-hotspot"