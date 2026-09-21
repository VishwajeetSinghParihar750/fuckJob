#!/bin/sh
set -eu

if [ "${DASHBOARD_AUTH_REALM:-off}" != "off" ]; then
  : "${DASHBOARD_BASIC_AUTH_USER:?Set DASHBOARD_BASIC_AUTH_USER when dashboard auth is enabled}"
  : "${DASHBOARD_BASIC_AUTH_PASSWORD:?Set DASHBOARD_BASIC_AUTH_PASSWORD when dashboard auth is enabled}"
  printf '%s\n' "$DASHBOARD_BASIC_AUTH_PASSWORD" \
    | htpasswd -i -c -B /etc/nginx/.htpasswd "$DASHBOARD_BASIC_AUTH_USER"
else
  rm -f /etc/nginx/.htpasswd
fi

exec /docker-entrypoint.sh "$@"
