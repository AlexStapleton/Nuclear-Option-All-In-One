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
printf '%s\n' '[Nuclear Option] Starting the game server…'
exec ./NuclearOptionServer.x86_64 -logFile - -limitframerate 60 -DedicatedServer /server/DedicatedServerConfig.json -ServerRemoteCommands 7779
