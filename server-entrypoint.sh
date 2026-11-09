#!/bin/bash
set -eu
launch_mode=${1:-update}
restart_id=${2:-}
exec 3>&1 4>&2

restart_state() {
    [[ -n $restart_id ]] || return 0
    (umask 022
     printf '%s %s\n' "$restart_id" "$1" > .panel-logs/.restart-state.tmp &&
     mv -f -- .panel-logs/.restart-state.tmp .panel-logs/restart-state) || :
}

fps=60
log_mib=1
log_files=2
if [[ -f .panel-launch-settings && ! -L .panel-launch-settings ]] &&
    IFS= read -r -n 64 settings < .panel-launch-settings &&
    [[ $settings =~ ^([1-9][0-9]{0,2})\ ([1-9][0-9]?)\ ([1-9][0-9]?)$ ]]; then
    if (( BASH_REMATCH[1] >= 10 && BASH_REMATCH[1] <= 240 &&
          BASH_REMATCH[2] <= 32 && BASH_REMATCH[3] <= 10 )); then
        fps=${BASH_REMATCH[1]}
        log_mib=${BASH_REMATCH[2]}
        log_files=${BASH_REMATCH[3]}
    fi
fi
log_limit=$((log_mib * 1048576))

prepare_logs() {
    umask 022
    mkdir -p .panel-logs && touch .panel-logs/server.log && chmod 0644 .panel-logs/server.log || return 1
    cd .panel-logs || return 1
    local name index
    for ((index=0; index<10; index++)); do
        name=server.log
        if ((index)); then name=server.log.$index; fi
        if ((index >= log_files)); then
            rm -f -- "$name" || return 1
        elif [[ -f $name ]] && (( $(wc -c < "$name") > log_limit )); then
            tail -c "$log_limit" -- "$name" > .trimmed-log && mv -f -- .trimmed-log "$name" || return 1
        fi
    done
}

copy_logs() {
    # Keep Docker stdout as well as a bounded copy for the panel. The -p option
    # keeps tee writing to Docker if the log-copy process loses its output.
    tee -p >(
        cd .panel-logs
        umask 022
        # mawk normally buffers pipe input before processing the first line.
        LC_ALL=C mawk -W interactive -v size="$(wc -c < server.log)" -v limit="$log_limit" -v files="$log_files" '
            function rotate(    i, source, dest) {
                close("server.log")
                if (files == 1)
                    return system(": > server.log")
                for (i=files-1; i>=1; i--) {
                    source = i == 1 ? "server.log" : "server.log." (i-1)
                    dest = "server.log." i
                    if (system("if [ -f " source " ]; then mv -f -- " source " " dest "; fi") != 0)
                        return 1
                }
                return 0
            }
            {
                line = substr($0, 1, 16384)
                bytes = length(line) + 1
                if (size + bytes > limit) {
                    if (rotate() != 0) exit 1
                    size = 0
                }
                print line >> "server.log"
                fflush("server.log")
                size += bytes
                prefix = "[Nuclear Option] Launch instance "
                if (index(line, prefix) == 1) instance = substr(line, length(prefix) + 1)
                sample = index(line, "[NOSTATS] {\"t\":\"perf\",")
                if (instance != "" && sample == 1) {
                    print instance, systime(), substr(line, sample + length("[NOSTATS] ")) > ".performance-sample.tmp"
                    close(".performance-sample.tmp")
                    system("mv -f -- .performance-sample.tmp performance-sample")
                }
            }
        '
    )
    wait "$!"
}

log_pid=
if (prepare_logs); then
    exec > >(copy_logs) 2>&1
    log_pid=$!
else
    printf '%s\n' 'Could not open the panel log. Output will still go to Docker.' >&2
fi
rm -f -- .panel-logs/restart-ready || :

flush_logs() {
    if [ -n "$log_pid" ]; then
        exec >&3 2>&4
        wait "$log_pid" || :
    fi
}

if [[ $launch_mode == update ]]; then
    restart_state updating
    printf '%s\n' '[Nuclear Option] Checking for a dedicated server update…'
    steamcmd +@ShutdownOnFailedCommand 1 +force_install_dir /server +login anonymous +app_update 3930080 +quit || {
        status=$?
        restart_state failed
        flush_logs
        exit "$status"
    }
fi
restart_state starting
if ! chmod +x ./NuclearOptionServer.x86_64; then
    restart_state failed
    flush_logs
    exit 1
fi
export LD_LIBRARY_PATH=/server/linux64

selected_mod_set() {
    local key
    if [[ -f .panel-mods/selected && ! -L .panel-mods/selected ]] &&
        IFS= read -r key < .panel-mods/selected && [[ $key =~ ^[a-f0-9]{32}$ ]]; then
        printf '%s' "$key"
    fi
}

after_action_launch_state() {
    if [[ -n $instance ]]; then
        (umask 022
         printf '%s %s\n' "$instance" "$1" > .panel-logs/.after-action.tmp &&
         mv -f -- .panel-logs/.after-action.tmp .panel-logs/after-action) || :
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
args=(-logFile - -limitframerate "$fps" -DedicatedServer /server/DedicatedServerConfig.json -ServerRemoteCommands 7779)
while :; do
    selected=$(selected_mod_set)
    profile=/server/.panel-mods/$selected
    report_root=/server/.panel-after-action
    report_profile=
    if [[ -z $selected && ! -L $report_root && -f $report_root/enabled && ! -L $report_root/enabled ]] &&
        IFS= read -r -n 32 report_loader < "$report_root/enabled" &&
        [[ $report_loader =~ ^loader\.[a-zA-Z0-9]{8}$ ]]; then
        report_profile=$report_root/$report_loader
    fi
    if instance=$(cat /proc/sys/kernel/random/uuid) &&
        (umask 022
         printf '%s\n' "$instance" > .panel-logs/.game-instance.tmp &&
         mv -f -- .panel-logs/.game-instance.tmp .panel-logs/game-instance &&
         printf '%s %s %s %s\n' "$instance" "$fps" "$log_mib" "$log_files" > .panel-logs/.launch-active.tmp &&
         mv -f -- .panel-logs/.launch-active.tmp .panel-logs/launch-active); then
        printf '[Nuclear Option] Launch instance %s\n' "$instance"
    else
        printf '%s\n' '[Nuclear Option] Could not publish launch status.' >&2
    fi
    if [[ -n $selected && ! -L .panel-mods && ! -L $profile && -f $profile/libdoorstop.so &&
          -f $profile/BepInEx/core/BepInEx.Preloader.dll ]]; then
        after_action_launch_state mod_set
        printf '[Nuclear Option] Starting with mod set %s.\n' "$selected"
        DOORSTOP_ENABLED=1 DOORSTOP_TARGET_ASSEMBLY="$profile/BepInEx/core/BepInEx.Preloader.dll" \
            LD_PRELOAD="$profile/libdoorstop.so" ./NuclearOptionServer.x86_64 "${args[@]}" &
    elif [[ -n $report_profile && ! -L $report_profile ]]; then
        if [[ -f $report_profile/libdoorstop.so &&
              -f $report_profile/BepInEx/core/BepInEx.Preloader.dll &&
              -f $report_profile/BepInEx/plugins/NuclearOption.AfterAction.dll &&
              -f $report_profile/game.sha256 ]] &&
            sha256sum --status -c "$report_profile/game.sha256"; then
            after_action_launch_state starting
            printf '%s\n' '[Nuclear Option] Starting with the server-only after-action collector.'
            NO_AAR_REPORT_DIR="$report_root/reports" NO_AAR_GAME_INSTANCE="$instance" DOORSTOP_ENABLED=1 \
                DOORSTOP_TARGET_ASSEMBLY="$report_profile/BepInEx/core/BepInEx.Preloader.dll" \
                LD_PRELOAD="$report_profile/libdoorstop.so" ./NuclearOptionServer.x86_64 "${args[@]}" &
        else
            after_action_launch_state rebuild
            printf '%s\n' '[Nuclear Option] After-action collector skipped. Rebuild it for this server installation.' >&2
            ./NuclearOptionServer.x86_64 "${args[@]}" &
        fi
    else
        after_action_launch_state off
        printf '%s\n' '[Nuclear Option] Starting the game server…'
        ./NuclearOptionServer.x86_64 "${args[@]}" &
    fi
    game_pid=$!
    restart_state launched
    restart_id=
    if [[ -n $instance ]]; then
        (umask 022
         printf '%s\n' "$instance" > .panel-logs/.restart-ready.tmp &&
         mv -f -- .panel-logs/.restart-ready.tmp .panel-logs/restart-ready) || :
    fi
    restart_requested=false
    while kill -0 "$game_pid" 2>/dev/null; do
        if [[ -n $instance && -f .panel-restart-request && ! -L .panel-restart-request ]] &&
            IFS=' ' read -r -n 128 id mode requested_instance < .panel-restart-request &&
            [[ $id =~ ^[a-f0-9]{32}$ && ( $mode == current || $mode == update ) && $requested_instance == "$instance" ]]; then
            restart_id=$id
            restart_state stopping
            printf '[Nuclear Option] Restart requested (%s).\n' "$mode"
            stop_game
            game_pid=
            flush_logs
            exec 3>&- 4>&-
            exec /bin/bash "$0" "$mode" "$id"
        fi
        if [[ $(selected_mod_set) != "$selected" ]]; then
            printf '%s\n' '[Nuclear Option] Restarting to apply the selected mod set…'
            stop_game
            restart_requested=true
            break
        fi
        sleep 2
    done
    if ! "$restart_requested" && [[ $(selected_mod_set) == "$selected" ]]; then
        status=0
        wait "$game_pid" || status=$?
        flush_logs
        exit "$status"
    fi
done
