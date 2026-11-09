#!/bin/sh
set -eu
cd "$(dirname "$0")"

compose_env=$(docker compose config --environment)
data_dir=$(printf '%s\n' "$compose_env" | sed -n 's/^SERVER_DATA_DIR=//p')
data_dir=${data_dir:-./server}
case $data_dir in
    /*|./*|../*) ;;
    *) data_dir=./$data_dir ;;
esac
data_dir=$(CDPATH= cd "$data_dir" && pwd -P)
root=$data_dir/.panel-after-action
if [ -L "$root" ]; then
    printf '%s\n' 'The after-action directory must be a regular directory.' >&2
    exit 1
fi
if [ "${1:-}" = --disable ]; then
    rm -f -- "$root/enabled"
    printf '%s\n' 'After-action collection will stop on the next game restart.'
    exit 0
fi
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: sh install-after-action.sh [--disable]' >&2
    exit 1
fi

managed=$data_dir/NuclearOptionServer_Data/Managed
if [ ! -f "$managed/Assembly-CSharp.dll" ]; then
    printf '%s\n' 'Start the dedicated server once so SteamCMD can install its game assemblies.' >&2
    exit 1
fi
umask 022
mkdir -p "$root/reports"
profile=$(mktemp -d "$root/loader.XXXXXXXX")
marker=
cleanup() {
    if [ -n "$profile" ]; then rm -rf -- "$profile"; fi
    if [ -n "$marker" ]; then rm -f -- "$marker"; fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM
chmod 0755 "$profile"

PYTHONPATH=panel python3 -B - "$profile" <<'PY'
import sys
from pathlib import Path

from server_mods import BEPINEX, EXTRACT_LIMIT, download, loader_ready, unpack_zip

profile = Path(sys.argv[1])
archive = profile / "loader.zip"
download(BEPINEX, archive)
unpack_zip(archive, profile, EXTRACT_LIMIT)
archive.unlink()
if not loader_ready(profile):
    raise SystemExit("The downloaded Linux BepInEx loader is incomplete.")
config = profile / "BepInEx" / "config"
config.mkdir(exist_ok=True)
(config / "BepInEx.cfg").write_text(
    "[Chainloader]\nHideManagerGameObject = true\n\n[Logging.Console]\nEnabled = true\n",
    encoding="ascii",
)
PY

docker run --rm --network none --user "$(id -u):$(id -g)" \
    -e DOTNET_CLI_HOME=/tmp -e DOTNET_NOLOGO=1 \
    --mount "type=bind,source=$(pwd -P)/after-action,target=/src,readonly" \
    --mount "type=bind,source=$managed,target=/game,readonly" \
    --mount "type=bind,source=$profile,target=/loader" \
    mcr.microsoft.com/dotnet/sdk:8.0 dotnet build /src/AfterAction.csproj -c Release \
    --artifacts-path /loader/build -o /loader/BepInEx/plugins \
    -p:GameManaged=/game -p:BepInExCore=/loader/BepInEx/core

test -f "$profile/BepInEx/plugins/NuclearOption.AfterAction.dll"
rm -rf -- "$profile/build"
(cd "$data_dir" && sha256sum NuclearOptionServer_Data/Managed/Assembly-CSharp.dll \
    NuclearOptionServer_Data/Managed/Mirage.dll) > "$profile/game.sha256"
marker=$(mktemp "$root/.enabled.XXXXXXXX")
printf '%s\n' "${profile##*/}" > "$marker"
chmod 0644 "$marker"
mv -f -- "$marker" "$root/enabled"
profile=
marker=
printf '%s\n' 'After-action collector installed. Restart the current build from the panel to enable it.'
