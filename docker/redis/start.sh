#!/bin/sh
set -eu
: "${REDIS_USERNAME:?Set REDIS_USERNAME}"
: "${REDIS_PASSWORD:?Set REDIS_PASSWORD}"
case "$REDIS_USERNAME" in *[!a-zA-Z0-9_-]*|'') echo 'Invalid Redis username' >&2; exit 1;; esac
password_hash=$(printf '%s' "$REDIS_PASSWORD" | sha256sum | cut -d ' ' -f 1)
umask 077
printf 'user default off\nuser %s on #%s ~* &* +@all\n' "$REDIS_USERNAME" "$password_hash" > /data/users.acl
exec redis-server --aclfile /data/users.acl --appendonly yes --appendfsync everysec --maxmemory-policy noeviction
