#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ "$(uname -m)" != x86_64 ]; then
    printf '%s\n' 'This bundle requires an x86-64 Linux host.' >&2
    exit 1
fi
docker compose version >/dev/null
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Install python3 so the launcher can read the game ports from the server config.' >&2
    exit 1
fi

if [ ! -e .panel.env ]; then
    password=$(od -An -N24 -tx1 /dev/urandom | tr -d '[:space:]')
    if [ "${#password}" -ne 48 ]; then
        printf '%s\n' 'Could not generate the panel password.' >&2
        exit 1
    fi
    (umask 077; set -C; printf 'PANEL_PASSWORD=%s\n' "$password" > .panel.env)
fi

compose_env=$(docker compose config --environment)
data_dir=$(printf '%s\n' "$compose_env" | sed -n 's/^SERVER_DATA_DIR=//p')
data_dir=${data_dir:-./server}
case $data_dir in
    /*|./*|../*) ;;
    *) data_dir="./$data_dir" ;;
esac

mkdir -p "$data_dir/missions"
if [ ! -e "$data_dir/DedicatedServerConfig.json" ]; then
    cp server-config.example.json "$data_dir/DedicatedServerConfig.json"
fi

udp_ports=$(python3 -B panel/server_options.py "$data_dir/DedicatedServerConfig.json")
read -r NO_GAME_PORT NO_QUERY_PORT <<EOF
$udp_ports
EOF
export NO_GAME_PORT NO_QUERY_PORT
docker compose up -d --build
panel_port=$(printf '%s\n' "$compose_env" | sed -n 's/^PANEL_PORT=//p')
if [ -f "$data_dir/.panel-password" ]; then
    printf '\n%s\n' 'Panel login is admin. Use the password you set in the web panel.'
else
    printf '\n%s\n' 'Panel login is admin. Read the generated password with: cat .panel.env'
fi
printf 'From your LAN, open http://LINUX_LAN_IP:%s in your browser.\n' "${panel_port:-5000}"
