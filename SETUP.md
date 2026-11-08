# Set up a Nuclear Option server

## 1. Prepare the Linux host

Use an x86-64 Linux machine with a current Docker Engine and the Docker Compose plugin. The account you use should be able to run Docker and write to the chosen data directory. Check that `uname -m` reports `x86_64` and `docker compose version` succeeds.

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

In a browser on Windows or any other device on your LAN, open `http://LINUX_LAN_IP:5000`. For example, if the Linux box's LAN address is `192.168.1.50`, open `http://192.168.1.50:5000`. Sign in as `admin` with the generated password. Once the game server is ready, the dashboard shows the current and next missions and the player roster.

If the Linux host already uses port 5000, set `PANEL_PORT` in `.env`, run `sh start.sh` again, and use the new port in the URL. The panel uses HTTP with password authentication. Use it on a trusted LAN and keep its TCP port off your router's forwarding list. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

### Rename the server and pick missions

Click **Settings & missions** in the dashboard navigation. Under **Game join password**, choose **Set a password** or **Remove password**, then click **Save and reload**. If the game browser still shows the previous settings, run `docker compose restart`. Use the mission picker to add entries to the rotation, reorder them, or change time limits. **Queue as next mission** sets a one-time override after the current mission ends. [Shockfront's command reference](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/tree/main/ServerCommands)

- **Built-in missions.** Use the presets or enter an exact mission name with the appropriate group if it is missing from the picker.
- **Local custom scenarios.** In the Windows mission editor, save a multiplayer mission. In the panel, use **Import a custom mission** to open the saved JSON or a ZIP of one mission folder. A ZIP keeps any companion files. Edit the fields the panel recognizes, such as the briefing, respawn rule, or weather, and choose **Save as copy**. The new mission is selected in the picker. Use **Queue as next mission** for a one-time run or **Add to rotation** followed by **Save and reload**. Uploads support up to 16 MiB, with ZIP contents up to 64 MiB unpacked. If you set a custom `MissionDirectory`, use a subfolder under `/server` so both containers can see it.
- **Steam Workshop.** Select `Workshop`, paste the Steam Community item link or its numeric ID, and add it to the rotation. Save first so the dedicated server can download the item. [Shockfront's mission format](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md#loading-missions)

If you are upgrading an existing installation, run `git pull` and `sh start.sh` to rebuild the panel. If the settings page reports a permissions error, check that the account owning the data folder can read the JSON file and write to the folder.

## 4. Let friends join from outside your network

Follow the [README's port and router setup](README.md#ports-and-router-setup) for the two UDP forwards, firewall pass rules, and the OPNsense hairpin NAT example.

## Day-to-day operation

Open **Moderation** to select a connected player or enter a Steam ID manually. Choose an action and review it before sending it. Expand **Ban and kick lists** for bulk controls, **Server maintenance** for the mission timer or config reload, and **Last command response** for troubleshooting.

Open **Server logs** in the panel for recent SteamCMD and game output. It refreshes every three seconds while the tab is visible. Use **Pause updates** to hold the current view or **Jump to latest** to scroll to the end. The Linux host keeps the current and previous log under `SERVER_DATA_DIR/.panel-logs/`, up to 1 MiB each. `docker compose logs nuclear-option` still shows the game container's output.

Run these commands from the project folder on Linux.

| Task | Command |
| --- | --- |
| Show container status | `docker compose ps` |
| Restart and check for game updates | `docker compose restart` |
| Stop the server and panel | `docker compose stop` |
| Start after stopping | `sh start.sh` |
| Follow game logs | `docker compose logs -f nuclear-option` |
| Follow panel logs | `docker compose logs -f panel` |

Back up your configured data directory, `.env` if you use it, and `.panel.env`. To rotate the panel password, edit `.panel.env` and run `docker compose up -d --force-recreate panel`.

## Move existing server data

1. Stop the original stack with `docker compose down` from its project folder.
2. Create the new host directory and copy the **contents** of the old data folder into it, including `DedicatedServerConfig.json`, the ban list, and `missions/`. The old default folder is `./server`.
3. Set `SERVER_DATA_DIR` in `.env` to the new path and run `sh start.sh`. The launcher preserves an existing configuration in that folder. If you point it at an empty directory, it creates a fresh configuration there.

Keep container paths such as `/server/missions` in the game JSON. If the old config points elsewhere for custom missions, copy those missions into the new data folder's `missions/` subdirectory and set `MissionDirectory` to `/server/missions`. If the config overrides the default game or query ports, match those values in the Compose port mappings.
