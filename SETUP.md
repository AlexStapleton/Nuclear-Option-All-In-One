# Set up a Nuclear Option server

## 1. Prepare the Linux host

Use an x86-64 Linux machine with Docker Engine, the Docker Compose plugin, and Python 3. The account you use should be able to run Docker and write to the chosen data directory. The launcher uses Python to read the game and Steam query ports from the saved server configuration.

## 2. Download and start the stack

Run these commands on Linux. You can also use GitHub's **Code → Download ZIP** and open a terminal in the extracted folder.

```sh
git clone https://github.com/AlexStapleton/Nuclear-Option-All-In-One.git
cd Nuclear-Option-All-In-One
```

The default panel URL is `http://LINUX_LAN_IP:5000` and the default data directory is `./server`. To customize either, run `cp .env.example .env` and edit `.env` before starting. For example:

```dotenv
PANEL_BIND_IP=192.168.1.50
PANEL_PORT=5000
SERVER_DATA_DIR='/mnt/games/Nuclear Option'
```

Use your Linux box's actual LAN IPv4 address for `PANEL_BIND_IP`. The default `0.0.0.0` listens on every IPv4 interface. On a host with both LAN and public interfaces, set the bind address to the LAN IPv4. Relative data paths are resolved from the project directory. The path is on the Linux host. Inside the game container it is still `/server`.

Start the stack and follow the game log:

```sh
sh start.sh
docker compose logs -f nuclear-option
```

The first run builds the panel and downloads the dedicated server through SteamCMD using an anonymous login. When the game log says it is waiting for players, open **Multiplayer** on your Windows PC and look for **Nuclear Option Home Server**. Press Ctrl+C to leave the log view.

The launcher creates `DedicatedServerConfig.json` in your data folder. Use **Settings & missions** to change the server name, join password, or mission rotation. You can also edit `server-config.example.json` before the first start. [Shockfront's configuration and mission guide](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md)

## 3. Open the admin panel from your LAN

On Linux, run `cat .panel.env` from the project folder and copy the value after `PANEL_PASSWORD=`. This password is separate from the in-game password.

In a browser on Windows or any other device on your LAN, open `http://LINUX_LAN_IP:5000`. For example, if the Linux box's LAN address is `192.168.1.50`, open `http://192.168.1.50:5000`. Enter `admin` and the generated password on the sign-in page. The session lasts up to 12 hours or until the panel restarts. Use **Sign out** in the navigation on a shared device. Once the game server is ready, the dashboard shows the current and next missions and the player roster.

After signing in, open **Change password**. Enter your current password and confirm a new one of at least 12 characters. The change signs out all panel sessions. Sign back in as `admin` with the new password.

If the Linux host already uses port 5000, set `PANEL_PORT` in `.env`, run `sh start.sh` again, and use the new port in the URL. The panel uses HTTP with password authentication. Use it on a trusted LAN and keep its TCP port off your router's forwarding list. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

### Rename the server and pick missions

Open **Settings & missions**. Use **Remove** on a mission's rotation row, then choose **Save rotation**. Expand **Edit rotation entry** to reorder it or change its time limit. **Queue next** sets a one-time override after the current mission ends. [Shockfront's command reference](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/tree/main/ServerCommands)

To end the current round early, click **Switch now** on the target mission's rotation row. Set a warning delay of 0–3,600 seconds and optionally enter a message. The panel remembers both choices in that browser. It broadcasts the announcement and any detected mod requirement to connected players before starting the countdown. Zero skips the warning countdown. The normal server transition delay still applies. The game provides no remote check of each player's installed mods. If the server is idle, the mission stays queued for the next load. [Shockfront's mission commands](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/ServerCommands/Readme.md#set-time-remaining)

Change the server name and game join password under **Server settings**, then click **Save server settings**. Expand **Advanced server options** for timing, ports, and other settings. If the game browser still shows the previous settings, run `docker compose restart`.

**Server options** covers maximum players, visibility, the modded flag, mission timing and storage, ban and exemption lists, automatic kicks, vote kicks, and UDP ports. To change the UDP ports, save the overrides and run `sh start.sh` on the Linux host. The launcher publishes the configured ports in Docker. Update any router forwards and firewall rules to match. If you change the mission directory, move the existing mission folders to the new location before saving. [Shockfront's configuration reference](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md#config-settings)

### Launch settings and performance

Use **Restart current build** to apply saved launch settings or a restored profile using the installed game. **Update and restart** runs SteamCMD before launching. Both actions disconnect active players and report progress in the panel. Changes to the Docker UDP mappings still require running `sh start.sh` on the host.

Expand **Performance history** to see the latest 120 NukeStats samples collected while the panel is running. Each row shows measured FPS, frame times, player and AI aircraft counts, and physics workers when the plugin reports them. A sample gets a mission name when its full measurement window can be attributed to that mission.

Open **Settings & missions → Launch settings**. Choose **30 FPS**, **60 FPS** (the existing default), or a custom cap between 10 and 240. The card shows the cap used by the last launch and indicates when a saved change is pending. You can also retain 1–10 game logs, each up to 1–32 MiB. These settings apply to the files in `.panel-logs`. Docker and BepInEx keep their own logs.

Under **Startup profiles**, save the current game config under a name. **Restore** replaces the saved game config and takes effect on restart. Profiles include the join password and mission rotation. Docker UDP mappings, launcher preferences, and the selected mod set are managed separately. Keep profile backups private because they contain the game join password.

**Physics workers & measured performance** is optional and requires a compatible NukeStats server plugin. For an existing BepInEx mod set, review the toolkit's features before adding its `NukeStats.dll` release to `.panel-mods/<selected-set>/BepInEx/plugins/NukeStats/`. Start that mod set once so BepInEx generates `BepInEx/config/anz.nukestats.cfg`. The panel edits only `Perf.JobWorkers` and `Perf.Timing` in that file. Zero uses the game's worker selection. Enable timing and restart to see measured FPS, average and 95th-percentile frame time, and the worker count as reports arrive, usually once a minute. NukeStats' own FPS override or frame pacer can supersede the launch cap during play. [NukeStats releases](https://github.com/Tomosnuclearbombos/nuclear-option-toolkit/releases) · [Server benchmarks](https://github.com/Tomosnuclearbombos/nuclear-option-tester)

### After-action reports

The optional collector runs in the dedicated server process with its own BepInEx installation. It observes the game's combat events without changing version checks or sending custom network messages. Leave the gameplay mod set unselected and use a stock mission to play with a regular Steam client.

1. Update the checkout and run `sh start.sh` during a maintenance window. Wait for the server to finish its Steam update.
2. Run `sh install-after-action.sh` on the Linux host. It checks the Linux loader download and compiles the collector against the installed dedicated server using a .NET SDK container.
3. Choose **Restart current build** under **Settings & missions → Launch settings**. In **Server logs**, look for **After-action collector ready**. Open **After-action reports** while a mission is running. The live scoreboard refreshes about every five seconds while the page is visible. When the mission ends, the scoreboard links to its finished report.

Finished reports add mission-wide totals, category leaders (including ties), and five-minute windows of player-credited combat. The game awards primary kill credit and separate assists. Enemy missiles that receive kill credit count as interceptions. The report separates player aircraft and AI aircraft, and counts player aircraft losses reported by the game. Completed reports live under `SERVER_DATA_DIR/.panel-after-action/reports/`. Older reports can still show their scoreboards and highlights. A graceful shutdown can save the current report as interrupted. After updating the panel for live reports, or if the game assemblies change, rerun the installer and restart the current build. Run `sh install-after-action.sh --disable` and restart to turn off collection while keeping report history.

Choose **Download player stats (CSV)** in any finished report to export the scoreboard. When importing into a spreadsheet, set the Steam ID column to text to preserve every digit.

The **Tacview replays** gallery shows the latest 40 recordings in `SERVER_DATA_DIR/replays/`. To record on the server, create that folder and install a NOBlackBox release compatible with your server into the BepInEx profile you actually launch. For the after-action collector, the profile directory name is in `SERVER_DATA_DIR/.panel-after-action/enabled` and lives beside that file. Put the release files under the profile’s `BepInEx/plugins/NOBlackBox/`. After the plugin generates its config, set its `OutputPath` to `/server/replays` and restart the game. If you rebuild the collector or switch BepInEx profiles, apply the recorder setup to the new profile as needed. You can also copy existing `.acmi` or `.acmi.zip` files directly into the replay folder. Download a recording from the gallery and open it in Tacview. [NOBlackBox setup and settings](https://github.com/KopterBuzz/NOBlackBox/blob/main/INSTALL.md)

### Mission sources
- **Built-in missions.** Use the presets or enter an exact mission name with the appropriate group if it is missing from the picker.
- **Local custom scenarios.** In the Windows mission editor, save a multiplayer mission. Under **Add missions**, expand **Import a mission file** to open the saved JSON or a ZIP of one mission folder. A ZIP keeps any companion files. Edit the fields the panel recognizes, such as the briefing, respawn rule, or weather, and choose **Save as copy**. The new mission is selected in the picker. Use **Queue once** for a one-time run or **Add to rotation** followed by **Save rotation**. Uploads support up to 16 MiB, with ZIP contents up to 64 MiB unpacked. If you set a custom `MissionDirectory`, use a subfolder under `/server` so both containers can see it.
- **Steam Workshop.** Paste an item link or choose `Workshop` and enter its numeric ID. **Add to rotation** saves the item directly and starts the server's native download. **Add and queue** also saves a new Workshop item to start its download, then queues it as soon as the file is ready if you keep the page open for up to two minutes. You can also queue it from its rotation row once the file is ready. If Steam requires access to a private item, export or download it with an account that has access, then import the mission JSON or ZIP as a local scenario. Use **Server logs** to troubleshoot downloads. [Shockfront's Workshop instructions](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md#workshop-missions)
- **Available missions.** The list next to **Add missions** includes built-in missions, local scenarios, and completed Workshop downloads, including items outside the rotation. Choose **Downloaded Workshop missions** in the source filter to see only those downloads. Use **Add** to put a mission in the rotation, or **Queue next** for a one-time run. Both use the time limit from **Add missions**.
- **Workshop mods and maps.** Open **Mod management** and choose a Workshop mission under **Install a Workshop mod set**. You can also click **Check Steam item and mods** in settings, then follow **Review in Mod Management**. The panel reads the mission author's mod list and looks up the packages, maps, and their catalog dependencies. Review the versions and explicitly choose a replacement if the requested version is missing. **Install and restart server** downloads the selected releases, checks their SHA-256 hashes, and restarts the game with the new set once staging finishes. A failed download leaves the current set selected. Progress and **Restart without mods** are in **Server mod set**. [NOMM's package catalog](https://github.com/KopterBuzz/NOMNOM)
- **Dependency inventory.** A **Foundation** supports another listed mod. A **Shared** mod serves multiple missions or scenarios. A **Mission specific** mod appears under one. **In server set** shows the version selected for the game launcher. The panel tracks the sets it installs. Expand a mod's details for dependent packages and rotation usage, or **Mission coverage** for scenarios with missing metadata. Use **Local scenario requirements** to record the packages a local scenario needs.
- **Mod updates.** Click **Check for updates** to refresh the NOMM catalog and compare releases with the server set. **Review update** shows which dependencies the release requires, any packages it would add or upgrade, and any different versions requested by tracked Workshop missions. **Apply update and restart** stages the revised set before switching the game server to it. For map updates, check the mission's expected build and coordinate the same version with players.
- **Version compatibility and rollback.** Expand a package's dependency details to see what other packages in the server set require. NOMNOM records minimum dependency versions and known conflicts. Check the author's release notes or test the combination when the catalog has incomplete information. Under **Version options**, pin the current version or review another catalog version. A pin belongs to that set and blocks dependency upgrades that would change it. **Save set without restarting** downloads a separate set for later. Open **Saved mod sets** to switch back to a previous set or activate the new one. Only one set loads at a time.
- **Clean reinstall.** Under **Saved mod sets**, open a set and choose **Download clean copy**. The panel downloads the recorded releases into a new directory with fresh BepInEx settings and caches. When the copy appears, use **Use this set and restart** to test it. The original remains available for rollback.
- Panel mod sets live under `.panel-mods` in your server data directory. The panel switches between whole sets, so check the selected set before queuing a mission that needs different mods. The catalog's game version is the version each mod was published for. Check **Server logs** after a switch and look at `.panel-mods/<set>/BepInEx/LogOutput.log` for loader and plugin diagnostics. For a custom map such as Swiss Alps, install the same map build on each player's game. Players can use NOMM to install their copies. [Swiss Alps installation](https://github.com/BatongMaster/NOSwissAlps#installing) · [NOMM releases](https://github.com/Combat787/NOMM/releases)

If you are upgrading an existing installation, run `git pull` and `sh start.sh` to rebuild the panel. If the settings page reports a permissions error, check that the account owning the data folder can read the JSON file and write to the folder.

## 4. Let friends join from outside your network

Follow the [README's port and router setup](README.md#ports-and-router-setup) for the two UDP forwards, firewall pass rules, and the OPNsense hairpin NAT example.

## Day-to-day operation

Open **Moderation** to select a connected player by their Steam display name and ID, or enter an ID manually. The **Bans & kicks** section lists saved ban entries and kicks issued through the panel during the current game server run, with Steam display names when available. The native game commands do not expose the in-memory ban list or vote kicks. Expand **Ban and kick controls** for bulk actions.

Open **Server logs** for recent SteamCMD and game output, or select **Mod loader (BepInEx)** for plugin startup and dependency errors from the selected server mod set. If a Windows client stalls during a custom map load, compare that output with `%USERPROFILE%\AppData\LocalLow\Shockfront\NuclearOption\Player.log`. Check that both sides loaded the required plugins and registered the same map build. The logs refresh every three seconds while the tab is visible. Use **Pause updates** to hold the view or **Jump to latest** to scroll to the end. Game log retention defaults to two 1 MiB files under `SERVER_DATA_DIR/.panel-logs/`.

Run these commands from the project folder on Linux.

| Task | Command |
| --- | --- |
| Show container status | `docker compose ps` |
| Restart and check for game updates | `docker compose restart` |
| Stop the server and panel | `docker compose stop` |
| Start after stopping | `sh start.sh` |
| Follow game logs | `docker compose logs -f nuclear-option` |
| Follow panel logs | `docker compose logs -f panel` |

Back up your configured data directory, `.env` if you use it, and `.panel.env`. The password you set in the panel is stored as a hash in `.panel-password` inside your server data directory. To recover access from the Linux host, stop the `panel` service with `docker compose stop panel`, move `.panel-password` out of the configured data directory, and run `sh start.sh`. Sign in with the password in `.panel.env`, then use **Change password** to choose a new one.

## Move existing server data

1. Stop the original stack with `docker compose down` from its project folder.
2. Create the new host directory and copy the **contents** of the old data folder into it, including `DedicatedServerConfig.json`, the ban list, and `missions/`. The old default folder is `./server`.
3. Set `SERVER_DATA_DIR` in `.env` to the new path and run `sh start.sh`. The launcher preserves an existing configuration in that folder. If you point it at an empty directory, it creates a fresh configuration there.

Keep container paths such as `/server/missions` in the game JSON. If the old config points elsewhere for custom missions, copy those missions into the new data folder's `missions/` subdirectory and set `MissionDirectory` to `/server/missions`. The launcher reads any game and query port overrides from the migrated configuration.
