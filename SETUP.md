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

The launcher creates `DedicatedServerConfig.json` in your data folder. Set `ServerName` and, for a private game, `Password` there. Run `docker compose restart` to apply those game settings. You can also edit `server-config.example.json` before the first start. Custom mission files can go in the data folder's `missions/` subdirectory. [Shockfront's configuration and mission guide](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md)

## 3. Open the admin panel from your LAN

On Linux, run `cat .panel.env` from the project folder and copy the value after `PANEL_PASSWORD=`. This password is separate from the in-game password.

In a browser on Windows or any other device on your LAN, open `http://LINUX_LAN_IP:5000`. For example, if the Linux box's LAN address is `192.168.1.50`, open `http://192.168.1.50:5000`. Sign in as `admin` with the generated password. Once the game server is ready, open the player list to verify that the panel can reach it.

If the Linux host already uses port 5000, set `PANEL_PORT` in `.env`, run `sh start.sh` again, and use the new port in the URL. The panel uses HTTP with password authentication. Use it on a trusted LAN and keep its TCP port off your router's forwarding list. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

## 4. Let friends join from outside your network

Allow and forward **UDP 7777** for gameplay and **UDP 7778** for server queries to your Linux box. Keep the panel port and TCP 7779 private. If an outside player can join while a player on your LAN cannot find the server, check whether your router supports NAT loopback. [Shockfront's port defaults](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md)

## Day-to-day operation

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
