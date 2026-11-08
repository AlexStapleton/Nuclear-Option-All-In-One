# Nuclear Option All-in-One

Run the official Nuclear Option dedicated server on an x86-64 Linux host and manage it from a browser on your Windows PC. Docker Compose runs the game server and Shockfront's web control panel together.

- **One-command start.** The launcher creates the initial server configuration, generates a panel password, and starts both containers. SteamCMD checks for game updates when the server container starts.
- **Browser administration.** List players, kick or ban them, send announcements, and choose the next mission. Use Docker Compose to start, stop, restart, and read the logs.
- **Private admin access.** The panel is available through an SSH tunnel. The game's remote-command port stays inside the stack.
- **Persistent server files.** Game data, settings, and custom missions live in `server/`.

## Quick start

On an x86-64 Linux host with Docker Engine, Docker Compose, and SSH access, run:

```sh
git clone https://github.com/AlexStapleton/Nuclear-Option-All-In-One.git
cd Nuclear-Option-All-In-One
sh start.sh
```

Continue with the [step-by-step setup guide](SETUP.md) to join the server, open the panel from Windows, and invite friends.

This is a community packaging project. The game server comes from Steam as app `3930080`. The control panel comes from Shockfront's Server Tools repository. [Official dedicated server guide](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/blob/main/DedicatedServerGuide.md) · [Shockfront's control panel](https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/tree/main/ServerControlPanel)
