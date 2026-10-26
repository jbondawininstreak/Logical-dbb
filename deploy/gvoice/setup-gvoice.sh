#!/usr/bin/env bash
# Google Voice texts for the CRM: a Matrix homeserver (Synapse) and the
# mautrix-gvoice bridge in Docker, next to the crm service that setup.sh made.
# Run as root on the same server, after setup.sh:
#   bash /opt/crm/deploy/gvoice/setup-gvoice.sh matrix.yourdomain.com
# Safe to run again: it keeps the existing config, users and data.
set -euo pipefail

DOMAIN="${1:-}"
APP_DIR="/opt/crm"
GV_DIR="/opt/gvoice"
MATRIX_USER="crm"
SYNAPSE_URL="http://127.0.0.1:8008"

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root (sudo bash setup-gvoice.sh matrix.yourdomain.com)"; exit 1
fi
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "Run deploy/setup.sh first: it creates $APP_DIR and its .env."; exit 1
fi
if [[ -z "$DOMAIN" ]]; then
  read -rp "Domain for the Matrix server (for example matrix.yourdomain.com): " DOMAIN
fi
if [[ -z "$DOMAIN" ]]; then
  echo "A domain is required."; exit 1
fi

set_env() {  # set_env KEY VALUE  -> in /opt/crm/.env, replacing the line if present
  local key="$1" value="$2"
  if grep -q "^${key}=" "$APP_DIR/.env"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$APP_DIR/.env"
  else
    printf '%s=%s\n' "$key" "$value" >> "$APP_DIR/.env"
  fi
}

echo "==> Installing Docker"
export DEBIAN_FRONTEND=noninteractive
if ! docker compose version >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q docker.io docker-compose-v2 || true
fi
if ! docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable -q --now docker

echo "==> Preparing $GV_DIR"
mkdir -p "$GV_DIR/synapse" "$GV_DIR/bridge"
install -m 644 "$APP_DIR/deploy/gvoice/docker-compose.yml" "$GV_DIR/docker-compose.yml"
echo "MATRIX_DOMAIN=$DOMAIN" > "$GV_DIR/.env"
cd "$GV_DIR"

if [[ ! -f synapse/homeserver.yaml ]]; then
  echo "==> Generating the Synapse config for $DOMAIN"
  docker compose run --rm --no-deps synapse generate
fi

if [[ ! -f bridge/config.yaml ]]; then
  echo "==> Generating the bridge config"
  docker compose run --rm --no-deps mautrix-gvoice >/dev/null
fi

echo "==> Pointing the bridge at Synapse"
# yq ships in the bridge image. Every value here is what the bridge needs to
# find Synapse inside the compose network and let the CRM's user log in to
# Google Voice; the rest of the config keeps the bridge's defaults.
docker compose run --rm --no-deps mautrix-gvoice yq -i "
  .homeserver.address = \"http://synapse:8008\" |
  .homeserver.domain = \"$DOMAIN\" |
  .appservice.address = \"http://mautrix-gvoice:29338\" |
  .appservice.hostname = \"0.0.0.0\" |
  .database.type = \"sqlite3-fk-wal\" |
  .database.uri = \"file:/data/mautrix-gvoice.db?_txlock=immediate\" |
  .bridge.permissions = {\"*\": \"relay\", \"$DOMAIN\": \"user\", \"@$MATRIX_USER:$DOMAIN\": \"admin\"} |
  .backfill.enabled = true |
  .logging.min_level = \"info\"
" /data/config.yaml

if [[ ! -f bridge/registration.yaml ]]; then
  echo "==> Generating the appservice registration"
  docker compose run --rm --no-deps mautrix-gvoice >/dev/null
fi
install -m 644 bridge/registration.yaml synapse/gvoice-registration.yaml

if ! grep -q "^app_service_config_files:" synapse/homeserver.yaml; then
  echo "==> Registering the bridge with Synapse"
  cat >> synapse/homeserver.yaml <<'YAML'

# Added by deploy/gvoice/setup-gvoice.sh: the Google Voice bridge.
app_service_config_files:
  - /data/gvoice-registration.yaml
YAML
fi

echo "==> Starting Synapse"
docker compose up -d synapse
for _ in $(seq 1 60); do
  curl -fs "$SYNAPSE_URL/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fs "$SYNAPSE_URL/health" >/dev/null || { echo "Synapse did not come up; see: docker compose -f $GV_DIR/docker-compose.yml logs synapse"; exit 1; }

PASSWORD="$(grep '^MATRIX_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2- || true)"
if [[ -z "$PASSWORD" ]]; then
  echo "==> Creating the Matrix user @$MATRIX_USER:$DOMAIN"
  PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
  docker compose exec -T synapse register_new_matrix_user \
    -c /data/homeserver.yaml -u "$MATRIX_USER" -p "$PASSWORD" --admin http://localhost:8008 \
    || { echo "Could not create @$MATRIX_USER:$DOMAIN. If it already exists, put its password in $APP_DIR/.env as MATRIX_PASSWORD and run this again."; exit 1; }
  set_env MATRIX_PASSWORD "$PASSWORD"
fi
set_env MATRIX_HOMESERVER "$SYNAPSE_URL"
set_env MATRIX_USER "$MATRIX_USER"
grep -q "^GVOICE_ANNOUNCE=" "$APP_DIR/.env" || set_env GVOICE_ANNOUNCE 1
# sed -i leaves the file owned by root; the crm service must still be able to read it.
chmod 600 "$APP_DIR/.env"
id -u crm >/dev/null 2>&1 && chown crm:crm "$APP_DIR/.env"

echo "==> Starting the bridge"
docker compose up -d

echo "==> Caddy: https://$DOMAIN"
mkdir -p /etc/caddy/conf.d
if ! grep -q "conf.d" /etc/caddy/Caddyfile; then
  printf '\nimport /etc/caddy/conf.d/*.caddy\n' >> /etc/caddy/Caddyfile
fi
cat > /etc/caddy/conf.d/matrix.caddy <<CADDY
# Written by deploy/gvoice/setup-gvoice.sh. Element and the CRM reach Synapse here.
$DOMAIN {
    handle /.well-known/matrix/client {
        header Content-Type application/json
        header Access-Control-Allow-Origin *
        respond \`{"m.homeserver":{"base_url":"https://$DOMAIN"}}\`
    }
    reverse_proxy 127.0.0.1:8008
}
CADDY
systemctl reload caddy || systemctl restart caddy

if systemctl is-enabled -q crm 2>/dev/null; then
  systemctl restart crm
  echo "==> crm service restarted (it now runs the Google Voice intake too)"
fi

cat <<NEXT

Done. Next steps:
  1. In cloaked.gg DNS, add an A record for $DOMAIN pointing at this server's IP.
  2. Open https://app.element.io, choose "Edit" next to the homeserver and enter
     https://$DOMAIN, then sign in as
        user:      $MATRIX_USER
        password:  $PASSWORD
  3. Start a chat with @gvoicebot:$DOMAIN and send:  login
  4. In a private browser window, sign in at https://voice.google.com, then
     paste the cookies the bot asks for (a JSON object with SID, HSID, SSID,
     OSID, APISID and SAPISID, or a request copied "as cURL" from the browser's
     network tab). The bot confirms and brings in your recent conversations.
  5. Texts now appear on the dashboard's Texts page and on each lead's page,
     and new numbers that text in become leads under the source google-voice.

Logs:    docker compose -f $GV_DIR/docker-compose.yml logs -f
         journalctl -u crm -f
Update:  cd $GV_DIR && docker compose pull && docker compose up -d
NEXT
