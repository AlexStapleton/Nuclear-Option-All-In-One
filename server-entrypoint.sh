#!/bin/bash
set -eu

copy_logs() {
    # Keep Docker stdout as well as a bounded copy for the panel. The -p option
    # keeps tee writing to Docker if the log-copy process loses its output.
    tee -p >(
        cd .panel-logs
        umask 022
        # mawk normally buffers pipe input before processing the first line.
        LC_ALL=C mawk -W interactive -v size="$(wc -c < server.log)" '
            {
                line = substr($0, 1, 16384)
                bytes = length(line) + 1
                if (size + bytes > 1048576) {
                    close("server.log")
                    if (system("mv -f -- server.log server.log.1") != 0)
                        exit 1
                    size = 0
                }
                print line >> "server.log"
                fflush("server.log")
                size += bytes
            }
        '
    )
    wait "$!"
}

log_pid=
if (umask 022; mkdir -p .panel-logs && touch .panel-logs/server.log && chmod 0644 .panel-logs/server.log); then
    exec > >(copy_logs) 2>&1
    log_pid=$!
else
    printf '%s\n' 'Could not open the panel log. Output will still go to Docker.' >&2
fi

printf '%s\n' '[Nuclear Option] Checking for a dedicated server update…'
steamcmd +@ShutdownOnFailedCommand 1 +force_install_dir /server +login anonymous +app_update 3930080 +quit || {
    status=$?
    if [ -n "$log_pid" ]; then
        # Docker stops the whole container when this shell exits. Flush the
        # log copy first so an early Steam error is visible in the panel.
        exec >&- 2>&-
        wait "$log_pid" || :
    fi
    exit "$status"
}
chmod +x ./NuclearOptionServer.x86_64
export LD_LIBRARY_PATH=/server/linux64

selected_mod_set() {
    local key
    if [[ -f .panel-mods/selected && ! -L .panel-mods/selected ]] &&
        IFS= read -r key < .panel-mods/selected && [[ $key =~ ^[a-f0-9]{32}$ ]]; then
        printf '%s' "$key"
    fi
}

stop_game() {
    kill -INT "$game_pid" 2>/dev/null || return 0
    for ((second=0; second<20; second++)); do
        kill -0 "$game_pid" 2>/dev/null || break
        sleep 1
    done
    kill -KILL "$game_pid" 2>/dev/null || :
    wait "$game_pid" || :
}

# Job control preserves SIGINT for the asynchronous game process. The panel can
# select a staged set through the shared data mount without access to Docker.
set -m
game_pid=
trap 'if [[ -n $game_pid ]]; then stop_game; fi; exit 0' INT TERM
args=(-logFile - -limitframerate 60 -DedicatedServer /server/DedicatedServerConfig.json -ServerRemoteCommands 7779)
while :; do
    selected=$(selected_mod_set)
    profile=/server/.panel-mods/$selected
    if ! (umask 022; cat /proc/sys/kernel/random/uuid > .panel-logs/.game-instance.tmp &&
          mv -f -- .panel-logs/.game-instance.tmp .panel-logs/game-instance); then
        printf '%s\n' '[Nuclear Option] Could not initialize panel kick tracking.' >&2
    fi
    if [[ -n $selected && ! -L .panel-mods && ! -L $profile && -f $profile/libdoorstop.so &&
          -f $profile/BepInEx/core/BepInEx.Preloader.dll ]]; then
        printf '[Nuclear Option] Starting with mod set %s.\n' "$selected"
        DOORSTOP_ENABLED=1 DOORSTOP_TARGET_ASSEMBLY="$profile/BepInEx/core/BepInEx.Preloader.dll" \
            LD_PRELOAD="$profile/libdoorstop.so" ./NuclearOptionServer.x86_64 "${args[@]}" &
    else
        printf '%s\n' '[Nuclear Option] Starting the game server…'
        ./NuclearOptionServer.x86_64 "${args[@]}" &
    fi
    game_pid=$!
    restart_requested=false
    while kill -0 "$game_pid" 2>/dev/null; do
        if [[ $(selected_mod_set) != "$selected" ]]; then
            printf '%s\n' '[Nuclear Option] Restarting to apply the selected mod set…'
            stop_game
            restart_requested=true
            break
        fi
        sleep 2
    done
    if ! "$restart_requested" && [[ $(selected_mod_set) == "$selected" ]]; then
        wait "$game_pid"
        exit $?
    fi
done
