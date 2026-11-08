#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ "$(uname -m)" != x86_64 ]; then
    printf '%s\n' 'This bundle requires an x86-64 Linux host.' >&2
    exit 1
fi
docker compose version >/dev/null

if [ ! -e .panel.env ]; then
    password=$(od -An -N24 -tx1 /dev/urandom | tr -d '[:space:]')
    if [ "${#password}" -ne 48 ]; then
        printf '%s\n' 'Could not generate the panel password.' >&2
        exit 1
    fi
    (umask 077; set -C; printf 'PANEL_PASSWORD=%s\n' "$password" > .panel.env)
fi

mkdir -p server/missions
if [ ! -e server/DedicatedServerConfig.json ]; then
    cp server-config.example.json server/DedicatedServerConfig.json
fi

docker compose up -d --build
printf '\n%s\n' 'Panel login is admin. Read the generated password with: cat .panel.env'
printf '%s\n' 'From Windows: ssh -N -L 127.0.0.1:5000:127.0.0.1:5000 USER@LINUX_IP'
printf '%s\n' 'Open http://127.0.0.1:5000 in your browser while SSH is connected.'
