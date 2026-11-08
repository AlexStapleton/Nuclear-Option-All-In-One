# Nuclear Option All-in-One

Run the official Nuclear Option dedicated server on an x86-64 Linux host and manage it from a browser on your Windows PC. Docker Compose runs the game server and Shockfront's web control panel together.

- **One-command start.** The launcher creates the initial server configuration, generates a panel password, and starts both containers. SteamCMD checks for game updates when the server container starts.
- **Browser administration.** Rename the server, edit its mission rotation, and queue the next mission with a picker for stock missions and local scenarios. Add Workshop missions by link or ID. The command panel also lists players, kicks or bans them, and sends announcements.
- **LAN admin access.** Open `http://LINUX_LAN_IP:5000` from your browser. The game's remote-command port stays inside the stack.
- **Configurable storage.** Game files, settings, and custom missions live in `./server` by default. Set `SERVER_DATA_DIR` in `.env` to choose another host folder.

## Quick start

On an x86-64 Linux host with Docker Engine and Docker Compose, run:

```sh
git clone https://github.com/AlexStapleton/Nuclear-Option-All-In-One.git
cd Nuclear-Option-All-In-One
sh start.sh
```

Open the web panel from any device on your LAN and sign in as `admin` with the password in `.panel.env`. To change the panel address, port, or storage folder, copy `.env.example` to `.env`, edit it, and rerun `sh start.sh`. See the [setup guide](SETUP.md) for configuration and migration steps.

This is a community packaging project. The game server comes from Steam as app `3930080`. The control panel comes from Shockfront's Server Tools repository. [Official dedicated server guide](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md) · [Shockfront's control panel](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/tree/main/ServerControlPanel)
