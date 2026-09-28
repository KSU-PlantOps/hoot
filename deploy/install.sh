#!/usr/bin/env bash
# HOOT installer for Raspberry Pi OS (Bookworm or later).
#
#   sudo ./deploy/install.sh
#   sudo ALTITUDE_M=300 ./deploy/install.sh     # set site elevation for CO2 compensation
#
# Idempotent: safe to re-run to upgrade an existing unit.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/hoot}"
SERVICE_USER="${SERVICE_USER:-hoot}"
I2C_BAUDRATE="${I2C_BAUDRATE:-50000}"
ALTITUDE_M="${ALTITUDE_M:-0}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

# ---- 1. system packages ----------------------------------------------------
log "installing system packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev python3-pil i2c-tools libjpeg-dev \
  rsync fonts-dejavu-core

# ---- 2. enable I2C at a cable-friendly speed --------------------------------
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt

if ! grep -q '^dtparam=i2c_arm=on' "$CONFIG_TXT"; then
  log "enabling I2C in $CONFIG_TXT"
  echo "dtparam=i2c_arm=on,i2c_arm_baudrate=${I2C_BAUDRATE}" >> "$CONFIG_TXT"
  REBOOT_REQUIRED=1
elif ! grep -q "i2c_arm_baudrate=${I2C_BAUDRATE}" "$CONFIG_TXT"; then
  # A 1 m probe lead needs a slower bus than the 100 kHz default; see
  # docs/HARDWARE.md "I2C over distance".
  log "setting I2C baudrate to ${I2C_BAUDRATE} for the probe lead"
  sed -i "s/^dtparam=i2c_arm=on.*/dtparam=i2c_arm=on,i2c_arm_baudrate=${I2C_BAUDRATE}/" "$CONFIG_TXT"
  REBOOT_REQUIRED=1
fi

# ---- 3. service user -------------------------------------------------------
if ! id "$SERVICE_USER" &>/dev/null; then
  log "creating service user '$SERVICE_USER'"
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -aG i2c,gpio "$SERVICE_USER" 2>/dev/null || warn "could not add $SERVICE_USER to i2c/gpio groups"

# ---- 4. code ---------------------------------------------------------------
log "installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if [[ "$REPO_DIR" != "$INSTALL_DIR" ]]; then
  # Preserve the unit's own config and trend data across upgrades.
  rsync -a --delete \
    --exclude '.venv' --exclude 'config.yaml' --exclude 'data' \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO_DIR"/ "$INSTALL_DIR"/
fi
mkdir -p "$INSTALL_DIR/data"

# ---- 5. virtualenv ---------------------------------------------------------
log "building virtualenv"
[[ -d "$INSTALL_DIR/.venv" ]] || python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements-pi.txt"

# ---- 6. config -------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/config.yaml" ]]; then
  log "generating config.yaml"
  # Device instance is derived from the Pi's serial so two units do not collide.
  "$INSTALL_DIR/.venv/bin/python" -m hoot -c "$INSTALL_DIR/config.yaml" init \
      --name "HOOT-$(hostname -s)" --altitude "$ALTITUDE_M"
  warn "review $INSTALL_DIR/config.yaml — especially bacnet.device_instance"
else
  log "keeping existing config.yaml"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ---- 7. systemd ------------------------------------------------------------
log "installing systemd unit"
install -m 0644 "$INSTALL_DIR/deploy/hoot.service" /etc/systemd/system/hoot.service
systemctl daemon-reload
systemctl enable hoot

if [[ "${REBOOT_REQUIRED:-0}" == "1" ]]; then
  warn "I2C settings changed — REBOOT before starting HOOT:  sudo reboot"
  warn "after reboot:  sudo systemctl start hoot"
else
  log "starting hoot"
  systemctl restart hoot
  sleep 3
  systemctl --no-pager --lines=15 status hoot || true
fi

cat <<EOF

  HOOT installed.

    config    $INSTALL_DIR/config.yaml
    web UI    http://$(hostname -I | awk '{print $1}'):8080
    logs      sudo journalctl -u hoot -f
    check     sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python -m hoot selftest
    bus scan  i2cdetect -y 1     (expect 3c=OLED  44=SHT45  48=TMP119  62=SCD41)

EOF
