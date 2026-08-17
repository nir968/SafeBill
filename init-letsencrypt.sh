#!/usr/bin/env bash
set -euo pipefail

# One-time bootstrap for the very first Let's Encrypt certificate. Run
# this once, by hand, BEFORE the first
# `docker compose -f docker-compose.prod.yml up -d` - it is not something
# the long-running `certbot` service in that file does on its own.
#
# Why this has to be a separate step (the chicken-and-egg problem):
# nginx/nginx.conf.template's 443 server block points ssl_certificate /
# ssl_certificate_key at /etc/letsencrypt/live/${DOMAIN}/... - and nginx
# refuses to start AT ALL (not just that one server block - the whole
# process) if those files don't exist yet. But certbot can't obtain a
# real certificate until nginx is already up and serving the ACME
# HTTP-01 challenge on port 80. This script breaks that cycle:
#   1. Generate a throwaway self-signed cert at the path nginx expects,
#      just so nginx has *something* to read and will actually start.
#   2. Start nginx.
#   3. Delete the throwaway cert.
#   4. Ask Let's Encrypt for a real one via the webroot method (nginx is
#      now up and can serve the challenge file certbot needs to prove
#      domain ownership).
#   5. Reload nginx so it picks up the real certificate.
# From that point on, the `certbot` service handles renewal automatically
# - see its own comments in docker-compose.prod.yml.
#
# Usage: ./init-letsencrypt.sh
# (run from the same directory as docker-compose.prod.yml)

if [ ! -f .env ]; then
    echo "No .env found - copy .env.example to .env and fill in DOMAIN/CERTBOT_EMAIL/etc. first." >&2
    exit 1
fi

set -a
source .env
set +a

: "${DOMAIN:?Set DOMAIN in .env first - e.g. DOMAIN=safebill.example.com}"
: "${CERTBOT_EMAIL:?Set CERTBOT_EMAIL in .env first - Let's Encrypt uses this for expiry/security notices}"

COMPOSE="docker compose -f docker-compose.prod.yml"

echo "==> [1/5] Creating a temporary self-signed certificate for ${DOMAIN}"
echo "    (only so nginx has something to read at startup - replaced below)"
$COMPOSE run --rm --entrypoint sh certbot -c "
  mkdir -p '/etc/letsencrypt/live/${DOMAIN}' &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '/etc/letsencrypt/live/${DOMAIN}/privkey.pem' \
    -out '/etc/letsencrypt/live/${DOMAIN}/fullchain.pem' \
    -subj '/CN=localhost'
"

echo "==> [2/5] Starting nginx with the temporary certificate"
$COMPOSE up -d nginx

echo "==> [3/5] Deleting the temporary certificate so certbot can request a real one"
$COMPOSE run --rm --entrypoint sh certbot -c "
  rm -rf '/etc/letsencrypt/live/${DOMAIN}' \
         '/etc/letsencrypt/archive/${DOMAIN}' \
         '/etc/letsencrypt/renewal/${DOMAIN}.conf'
"

echo "==> [4/5] Requesting a real certificate from Let's Encrypt for ${DOMAIN}"
echo "    (this needs ${DOMAIN} already pointing at this server's public IP, and port 80 reachable from the internet)"
$COMPOSE run --rm --entrypoint certbot certbot certonly \
  --webroot --webroot-path=/var/www/certbot \
  --email "${CERTBOT_EMAIL}" -d "${DOMAIN}" \
  --agree-tos --no-eff-email

echo "==> [5/5] Reloading nginx to pick up the real certificate"
$COMPOSE exec nginx nginx -s reload

echo
echo "Done. https://${DOMAIN} should now be serving a real, trusted certificate."
echo "Bring up the rest of the stack with: ${COMPOSE} up -d"
