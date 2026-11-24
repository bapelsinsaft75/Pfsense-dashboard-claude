#!/bin/sh
# Byter API-adressen i de byggda JS-filerna vid start.
# API_URL oförändrad (standard) => appen pratar direkt med https://api.filmdilemma.com
# API_URL=""                     => appen anropar /api/... på samma värd, nginx proxar till API_UPSTREAM
set -e
ORIG="https://api.filmdilemma.com"
HTML=/usr/share/nginx/html
STAMP=/tmp/.api-url-applied
TARGET="${API_URL-$ORIG}"
if [ -f "$STAMP" ]; then
  echo "API-adress redan satt till: $(cat $STAMP)"; exit 0
fi
if [ "$TARGET" != "$ORIG" ]; then
  echo "Sätter API-adress: '$ORIG' -> '$TARGET'"
  grep -rl "$ORIG" "$HTML/static/js" "$HTML/index.html" | while read -r f; do
    sed -i "s#$ORIG#$TARGET#g" "$f"
  done
fi
printf '%s' "$TARGET" > "$STAMP"

# DNS-resolver för nginx (behövs när proxy_pass använder en variabel)
NS=$(awk '/^nameserver/{ip=$2; if (ip ~ /:/) ip="[" ip "]"; printf "%s ", ip}' /etc/resolv.conf)
[ -n "$NS" ] || NS="1.1.1.1 8.8.8.8"
echo "resolver $NS valid=300s ipv6=off;" > /etc/nginx/conf.d/00-resolver.conf
