# Set up a Nuclear Option server

## 1. Prepare the Linux host

Use an x86-64 Linux machine with a current Docker Engine and the Docker Compose plugin. Enable SSH if you want to manage the server from another machine. The account you use should be able to run Docker. Check that `uname -m` reports `x86_64` and `docker compose version` succeeds.

## 2. Download and start the stack

Run these commands on Linux. You can also use GitHub's **Code → Download ZIP** and open a terminal in the extracted folder.

```sh
git clone https://github.com/AlexStapleton/Nuclear-Option-All-In-One.git
cd Nuclear-Option-All-In-One
sh start.sh
docker compose logs -f nuclear-option
```

The first run builds the panel and downloads the dedicated server through SteamCMD using an anonymous login. When the game log says it is waiting for players, open **Multiplayer** on your Windows PC and look for **Nuclear Option Home Server**. Press Ctrl+C to leave the log view.

The launcher creates `server/DedicatedServerConfig.json`. Set `ServerName` and, for a private game, `Password` there. Run `docker compose restart` to apply the changes. You can also edit `server-config.example.json` before the first start. Custom mission files can go under `server/missions`. [Shockfront's configuration and mission guide](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md)

## 3. Open the admin panel from Windows

On Linux, run `cat .panel.env` from the project folder and copy the value after `PANEL_PASSWORD=`. This password is separate from the in-game password.

In Windows PowerShell, replace `USER` and `LINUX_IP` with your Linux login and the server's LAN address. Keep the SSH session open while using the panel.

```powershell
ssh -N -L 127.0.0.1:5000:127.0.0.1:5000 USER@LINUX_IP
```

Open `http://127.0.0.1:5000` in your browser and sign in as `admin` with the generated password. Once the game server is ready, open the player list to verify that the panel can reach it.

If Windows port 5000 is busy, change the first port in the SSH mapping to `15000` and open `http://127.0.0.1:15000`. If Linux port 5000 is busy, change the middle port in `127.0.0.1:5000:5000/tcp` in `compose.yaml`, run `sh start.sh` again, and use the new Linux port at the end of the SSH mapping.

## 4. Let friends join from outside your network

Allow and forward **UDP 7777** for gameplay and **UDP 7778** for server queries to your Linux box. Keep TCP 5000 and 7779 private. If an outside player can join while a player on your LAN cannot find the server, check whether your router supports NAT loopback. [Shockfront's port defaults](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md)

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

Back up `server/` and `.panel.env`. To rotate the panel password, edit `.panel.env` and run `docker compose up -d --force-recreate panel`.

## Move an existing Compose server

Stop the original stack with `docker compose down` from its project folder. Copy its `server/` directory into this project before running `sh start.sh`. The launcher preserves an existing server configuration. If the config points outside `/server` for custom missions, copy them into `server/missions` and set `MissionDirectory` to `/server/missions`. If the config overrides the default game or query ports, match those values in the Compose port mappings.
