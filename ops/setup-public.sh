#!/usr/bin/env bash
# Public serving for EWB Live: https://<EWB_PUBLIC_HOSTNAME> via a Cloudflare
# Tunnel -> local nginx (cache + security headers) -> ewb_live.serve.
#
# HARD RULE (same guard as ops/install.sh): installs only on the ewb LXC as
# root (override for evaluation elsewhere: EWB_OPS_ALLOW_ANY=1).
#
#   ops/setup-public.sh install        install nginx+cloudflared, render the
#                                      proxy front + tunnel config/unit.
#                                      Needs NO credentials; the tunnel
#                                      connector unit is only enabled once
#                                      credentials exist.
#   ops/setup-public.sh finish-tunnel  run AFTER the owner authorisation
#                                      (cert.pem from `cloudflared tunnel
#                                      login`, or CLOUDFLARE_API_TOKEN in the
#                                      env): create the tunnel, point the
#                                      DNS record at it, enable the unit.
#   ops/setup-public.sh status         proxy/tunnel/public-health summary
#   ops/setup-public.sh uninstall      remove units + rendered configs
#
# Env (portable defaults, no box-specific values baked in):
#   EWB_PUBLIC_HOSTNAME  public hostname        (default: ewb.espr.ai)
#   EWB_TUNNEL_NAME      cloudflared tunnel     (default: ewb-live)
#   EWB_WEB_PORT         ewb_live.serve port    (default: 8050)
#   EWB_PROXY_PORT       nginx front port       (default: 8080)
#   EWB_PROXY_LISTEN     nginx front bind       (default: 127.0.0.1; use
#                        0.0.0.0 only if the tunnel connector runs on another box)
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_DIR="$OPS_DIR/public"
UNIT_DIR=/etc/systemd/system
CF_DIR=/etc/cloudflared

HOSTNAME_PUB="${EWB_PUBLIC_HOSTNAME:-ewb.espr.ai}"
TUNNEL_NAME="${EWB_TUNNEL_NAME:-ewb-live}"
WEB_PORT="${EWB_WEB_PORT:-8050}"
PROXY_PORT="${EWB_PROXY_PORT:-8080}"
PROXY_LISTEN="${EWB_PROXY_LISTEN:-127.0.0.1}"   # EWB_PROXY_LISTEN=0.0.0.0 for a remote connector
CREDS="$CF_DIR/$TUNNEL_NAME.json"

require_ewb_root() {
  if [ "$(whoami)" != root ] || { [ "$(hostname)" != ewb ] && [ -z "${EWB_OPS_ALLOW_ANY:-}" ]; }; then
    echo "EWB public front installs only on the ewb LXC as root (host '$(hostname)', user '$(whoami)')." >&2
    echo "Run on the box: ssh root@<ewb-box> 'cd /opt/ewb-live && ./ops/setup-public.sh install'" >&2
    exit 2
  fi
}

render() { # src dest
  sed -e "s|__PUBLIC_HOSTNAME__|$HOSTNAME_PUB|g" \
      -e "s|__TUNNEL_NAME__|$TUNNEL_NAME|g" \
      -e "s|__WEB_PORT__|$WEB_PORT|g" \
      -e "s|__PROXY_PORT__|$PROXY_PORT|g" \
      -e "s|__PROXY_LISTEN__|$PROXY_LISTEN|g" "$1" > "$2"
  echo "rendered $2"
}

install_front() {
  require_ewb_root
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  command -v nginx >/dev/null || apt-get install -y -qq nginx >/dev/null
  if ! command -v cloudflared >/dev/null; then
    arch=$(dpkg --print-architecture)
    case "$arch" in
      amd64) cf_arch=amd64 ;;
      arm64) cf_arch=arm64 ;;
      *) echo "unsupported arch $arch for cloudflared" >&2; exit 1 ;;
    esac
    curl -fsSL -o /tmp/cloudflared.deb \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$cf_arch.deb"
    apt-get install -y -qq /tmp/cloudflared.deb >/dev/null && rm -f /tmp/cloudflared.deb
    echo "installed cloudflared $(cloudflared --version | awk '{print $2}')"
  fi

  mkdir -p "$CF_DIR" /var/cache/nginx/ewb-live
  render "$PUBLIC_DIR/ewb-live-security.headers.conf" /etc/nginx/snippets/ewb-live-security.conf
  render "$PUBLIC_DIR/ewb-live-proxy.nginx.conf" /etc/nginx/conf.d/ewb-live.conf
  # our front binds loopback only; drop the distro welcome site (we installed
  # nginx, so its default vhost on :80 is ours to remove)
  if [ -L /etc/nginx/sites-enabled/default ]; then rm /etc/nginx/sites-enabled/default; fi
  nginx -t                              # strict: a bad render aborts the install
  systemctl enable --now nginx
  systemctl restart nginx   # reload does not rebind a changed listen addr (seen live)
  echo "nginx front live: http://127.0.0.1:$PROXY_PORT/ -> 127.0.0.1:$WEB_PORT"

  render "$PUBLIC_DIR/cloudflared-config.yml" "$CF_DIR/ewb-live.yml"
  render "$PUBLIC_DIR/ewb-live-tunnel.service" "$UNIT_DIR/ewb-live-tunnel.service"
  systemctl daemon-reload
  if [ -f "$CREDS" ]; then
    systemctl enable --now ewb-live-tunnel.service
    echo "tunnel connector enabled"
  else
    systemctl disable ewb-live-tunnel.service 2>/dev/null || true
    echo
    echo "tunnel connector installed but NOT enabled: no credentials yet."
    echo "Needs the product owner (see notes/infra_domain.md OWNER ACTION NEEDED):"
    echo "  run 'cloudflared tunnel login' on this box and have the owner click the URL,"
    echo "  then: ops/setup-public.sh finish-tunnel"
  fi
}

finish_tunnel() {
  require_ewb_root
  if [ ! -f "$CF_DIR/cert.pem" ] && [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    echo "no Cloudflare authorisation found: expected $CF_DIR/cert.pem (from" >&2
    echo "'cloudflared tunnel login') or CLOUDFLARE_API_TOKEN in the environment." >&2
    exit 3
  fi
  if [ ! -f "$CREDS" ]; then
    cloudflared tunnel create "$TUNNEL_NAME" --credentials-file "$CREDS"
    chmod 600 "$CREDS"
    echo "created tunnel $TUNNEL_NAME"
  fi
  cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_PUB"
  systemctl enable --now ewb-live-tunnel.service
  echo "tunnel running; DNS record for $HOSTNAME_PUB pointed at it."
  sleep 5
  echo "public check: curl -sI https://$HOSTNAME_PUB/ | head -5"
}

status() {
  printf 'nginx:    %s\n' "$(systemctl is-active nginx 2>/dev/null || true)"
  printf 'tunnel:   %s (enabled: %s)\n' \
    "$(systemctl is-active ewb-live-tunnel 2>/dev/null || true)" \
    "$(systemctl is-enabled ewb-live-tunnel 2>/dev/null || true)"
  printf 'creds:    %s\n' "$([ -f "$CREDS" ] && echo present || echo MISSING)"
  printf 'cert.pem: %s\n' "$([ -f "$CF_DIR/cert.pem" ] && echo present || echo MISSING)"
  command -v cloudflared >/dev/null && echo "cloudflared $(cloudflared --version | awk '{print $3}')"
  echo "-- loopback front:"
  curl -s -o /dev/null -w "  http://127.0.0.1:$PROXY_PORT/ -> %{http_code}\n" -m 5 "http://127.0.0.1:$PROXY_PORT/" || true
  echo "-- public edge:"
  curl -s -o /dev/null -w "  https://$HOSTNAME_PUB/ -> %{http_code}\n" -m 15 "https://$HOSTNAME_PUB/" || true
}

uninstall() {
  require_ewb_root
  systemctl disable --now ewb-live-tunnel.service 2>/dev/null || true
  rm -f "$UNIT_DIR/ewb-live-tunnel.service" \
        /etc/nginx/conf.d/ewb-live.conf \
        /etc/nginx/snippets/ewb-live-security.conf \
        "$CF_DIR/ewb-live.yml"
  # tunnel credentials and cert.pem are NOT deleted (revocation is an owner call)
  systemctl daemon-reload
  nginx -t && systemctl reload nginx || true
  echo "public front uninstalled (cloudflared package left in place)"
}

case "${1:-install}" in
  install) install_front ;;
  finish-tunnel) finish_tunnel ;;
  --status|status) status ;;
  --uninstall|uninstall) uninstall ;;
  *) echo "usage: ops/setup-public.sh [install|finish-tunnel|status|uninstall]" >&2; exit 2 ;;
esac
