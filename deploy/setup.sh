#!/usr/bin/env bash
# One-shot VPS setup for the Telegram CRM on Ubuntu 22.04 / 24.04.
# Run as root:  bash setup.sh crm.yourdomain.com
# Safe to run again: it updates the code and restarts the service.
set -euo pipefail

DOMAIN="${1:-}"
REPO="https://github.com/jbondawininstreak/Logical-dbb"
APP_DIR="/opt/crm"
APP_USER="crm"

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root (sudo bash setup.sh crm.yourdomain.com)"; exit 1
fi
if [[ -z "$DOMAIN" ]]; then
  read -rp "Domain for the dashboard (for example crm.yourdomain.com): " DOMAIN
fi
if [[ -z "$DOMAIN" ]]; then
  echo "A domain is required."; exit 1
fi

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git ufw curl \
  debian-keyring debian-archive-keyring apt-transport-https gnupg

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

echo "==> Creating user and fetching code"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull -q
else
  git clone -q "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created $APP_DIR/.env from the example. Fill it in before starting."
fi
chmod 600 .env
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Configuring Caddy for $DOMAIN"
install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
mkdir -p /etc/caddy /etc/caddy/conf.d
# The Caddyfile imports /etc/caddy/conf.d/*.caddy; keep one file there so the
# import always has something to match.
[[ -n "$(ls -A /etc/caddy/conf.d 2>/dev/null)" ]] || echo "# extra sites go here, one file each" > /etc/caddy/conf.d/00-empty.caddy
echo "CRM_DOMAIN=$DOMAIN" > /etc/caddy/environment
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/environment.conf <<'UNIT'
[Service]
EnvironmentFile=/etc/caddy/environment
UNIT
systemctl daemon-reload
systemctl enable -q caddy
systemctl restart caddy

echo "==> Installing the crm service"
install -m 644 deploy/crm.service /etc/systemd/system/crm.service
systemctl daemon-reload
systemctl enable -q crm

echo "==> Firewall: SSH, 80 and 443 only"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

if grep -q "paste-" .env; then
  echo
  echo "Next steps:"
  echo "  1. nano $APP_DIR/.env   (put in the two bot tokens, your user ID, the Turso URL and token, and a DASHBOARD_KEY)"
  echo "  2. systemctl start crm"
else
  systemctl restart crm
  echo "==> crm service restarted"
  echo
  echo "Next steps:"
fi
echo "  3. In cloaked.gg DNS, add an A record for $DOMAIN pointing at this server's IP, with the proxy turned on."
echo "  4. Open https://$DOMAIN/?key=<your DASHBOARD_KEY>"
echo
echo "Logs:   journalctl -u crm -f      Update later: bash $APP_DIR/deploy/setup.sh $DOMAIN"
