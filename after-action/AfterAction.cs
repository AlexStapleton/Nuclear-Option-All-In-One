using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using BepInEx;
using HarmonyLib;
using Mirage;
using Newtonsoft.Json;
using NuclearOption.DedicatedServer;
using NuclearOption.Networking;
using NuclearOption.SavedMission;

[BepInPlugin(PluginId, "After-action reports", "0.2.1")]
public sealed class AfterAction : BaseUnityPlugin
{
    private const string PluginId = "io.github.alexstapleton.nuclearoption.afteraction";
    private static AfterAction instance;
    private Harmony harmony;
    private string directory;
    private string gameInstance;
    private Report report;
    private bool warned;
    private readonly Stopwatch missionClock = new Stopwatch();
    private long nextSnapshot;
    private readonly Dictionary<ulong, PlayerResult> players = new Dictionary<ulong, PlayerResult>();
    private readonly Dictionary<PersistentID, HashSet<ulong>> assists = new Dictionary<PersistentID, HashSet<ulong>>();
    private readonly SortedDictionary<int, CombatWindow> timeline = new SortedDictionary<int, CombatWindow>();

    private void Awake()
    {
        directory = Environment.GetEnvironmentVariable("NO_AAR_REPORT_DIR");
        gameInstance = Environment.GetEnvironmentVariable("NO_AAR_GAME_INSTANCE");
        if (string.IsNullOrEmpty(directory) || Array.IndexOf(Environment.GetCommandLineArgs(), "-DedicatedServer") < 0)
            return;

        try
        {
            Directory.CreateDirectory(directory);
            harmony = new Harmony(PluginId);
            harmony.PatchAll(typeof(AfterAction).Assembly);
            instance = this;
            Logger.LogInfo("After-action collector ready.");
            try { PublishFile("collector-ready", gameInstance ?? string.Empty); }
            catch (Exception error) { Warn(error); }
        }
        catch (Exception error)
        {
            harmony?.UnpatchSelf();
            Logger.LogError($"Could not start after-action collection: {error.Message}");
        }
    }

    private void OnApplicationQuit()
    {
        if (instance == this)
            Finish("interrupted");
    }

    private void StartReport(Mission mission)
    {
        Finish("interrupted");
        warned = false;
        report = new Report
        {
            id = Guid.NewGuid().ToString("N"),
            mission = mission.Name,
            started_utc = DateTime.UtcNow.ToString("O"),
            game_instance = gameInstance,
            status = "live"
        };
        missionClock.Restart();
        nextSnapshot = 0;
        PublishLive();
    }

    private void PublishLive()
    {
        if (report == null || string.IsNullOrEmpty(gameInstance) || missionClock.ElapsedMilliseconds < nextSnapshot)
            return;

        nextSnapshot = missionClock.ElapsedMilliseconds + 5000;
        try
        {
            var network = NetworkManagerNuclearOption.i;
            if (network != null && network.Server != null)
                foreach (var connection in network.Server.AuthenticatedPlayers)
                    if (!connection.IsHost && connection.TryGetPlayer<Player>(out var player))
                        GetPlayer(player);
            CopyPlayers();
            report.updated_utc = DateTime.UtcNow.ToString("O");
            WriteReport("live.json");
        }
        catch (Exception error) { Warn(error); }
    }

    private void CopyPlayers()
    {
        report.players.Clear();
        report.players.AddRange(players.Values);
    }

    private void WriteReport(string filename)
    {
        PublishFile(filename, JsonConvert.SerializeObject(report));
    }

    private void PublishFile(string filename, string content)
    {
        string file = Path.Combine(directory, filename);
        string temporary = file + ".tmp";
        File.WriteAllText(temporary, content);
        if (File.Exists(file))
            File.Replace(temporary, file, null);
        else
            File.Move(temporary, file);
    }

    private void Finish(string status)
    {
        if (report == null)
            return;

        try
        {
            report.ended_utc = DateTime.UtcNow.ToString("O");
            report.status = status;
            CopyPlayers();
            report.timeline.AddRange(timeline.Values);
            WriteReport(report.id + ".json");
        }
        catch (Exception error)
        {
            Logger.LogWarning($"Could not save the after-action report: {error.Message}");
        }
        finally
        {
            report = null;
            missionClock.Stop();
            players.Clear();
            assists.Clear();
            timeline.Clear();
            try { File.Delete(Path.Combine(directory, "live.json")); }
            catch (Exception error) { Warn(error); }
        }
    }

    private PlayerResult GetPlayer(Player player)
    {
        if (report == null || player == null || player.SteamID == 0)
            return null;
        if (players.TryGetValue(player.SteamID, out var result))
            return result;

        string steamId = player.SteamID.ToString();
        result = new PlayerResult { steam_id = steamId, name = steamId };
        players.Add(player.SteamID, result);
        try
        {
            string name = player.GetPlayerName().RawSteamName;
            if (!string.IsNullOrWhiteSpace(name))
                result.name = name.Substring(0, Math.Min(name.Length, 96));
        }
        catch (Exception)
        {
            // A disconnected player may have no live name. Their Steam ID still identifies the row.
        }
        return result;
    }

    private void RecordCredit(Player player, Unit target)
    {
        if (target == null || GetPlayer(player) == null)
            return;
        if (!assists.TryGetValue(target.persistentID, out var contributors))
        {
            contributors = new HashSet<ulong>();
            assists.Add(target.persistentID, contributors);
        }
        contributors.Add(player.SteamID);
    }

    private void RecordKill(PersistentID killerID, PersistentID killedID, KillType killedType)
    {
        if (report == null)
            return;
        assists.TryGetValue(killedID, out var contributors);
        assists.Remove(killedID);
        if (!UnitRegistry.TryGetPersistentUnit(killedID, out var victim))
            return;

        var pilot = killedType == KillType.Aircraft
            ? GetPlayer((victim.unit as Aircraft)?.Player ?? victim.player)
            : null;
        if (pilot != null)
            pilot.deaths++;

        PlayerResult killer = null;
        if (UnitRegistry.TryGetPersistentUnit(killerID, out var attacker)
            && victim.GetHQ() != null && attacker.GetHQ() != victim.GetHQ())
            killer = GetPlayer(attacker.player);

        if (killer != null)
        {
            // Keep at most one day of five-minute windows plus an overflow window.
            int minute = (int)Math.Min(1440, missionClock.ElapsedMilliseconds / 300000 * 5);
            if (!timeline.TryGetValue(minute, out var window))
            {
                window = new CombatWindow { minute = minute };
                timeline.Add(minute, window);
            }
            switch (killedType)
            {
                case KillType.Aircraft:
                    if (pilot != null) killer.player_aircraft++;
                    else killer.ai_aircraft++;
                    window.aircraft++;
                    break;
                case KillType.Building: killer.buildings++; window.surface++; break;
                case KillType.Vehicle: killer.vehicles++; window.surface++; break;
                case KillType.Ship: killer.ships++; window.surface++; break;
                case KillType.Missile: killer.missiles++; window.missiles++; break;
            }
        }
        if (contributors != null)
            foreach (ulong contributor in contributors)
                if (players.TryGetValue(contributor, out var player) && player != killer)
                    player.assists++;
    }

    private void Warn(Exception error)
    {
        if (warned)
            return;
        warned = true;
        Logger.LogWarning($"Could not record an after-action event: {error.Message}");
    }

    [HarmonyPatch(typeof(NetworkManagerNuclearOption), nameof(NetworkManagerNuclearOption.ServerMissionStart))]
    private static class StartPatch
    {
        private static void Postfix(Mission mission)
        {
            try { instance?.StartReport(mission); }
            catch (Exception error) { instance?.Warn(error); }
        }
    }

    [HarmonyPatch(typeof(DedicatedServerManager), "GameShouldStop")]
    private static class EndPatch
    {
        private static void Postfix(bool __result)
        {
            if (__result)
                instance?.Finish("completed");
            else
                instance?.PublishLive();
        }
    }

    [HarmonyPatch(typeof(Player), "OnStartServer")]
    private static class JoinPatch
    {
        private static void Postfix(Player __instance)
        {
            try { instance?.GetPlayer(__instance); }
            catch (Exception error) { instance?.Warn(error); }
        }
    }

    [HarmonyPatch(typeof(FactionHQ), nameof(FactionHQ.ReportKillAction))]
    private static class CreditPatch
    {
        private static void Postfix(Player __0, Unit __1)
        {
            try { instance?.RecordCredit(__0, __1); }
            catch (Exception error) { instance?.Warn(error); }
        }
    }

    [HarmonyPatch(typeof(MessageManager), nameof(MessageManager.RpcKillMessage))]
    private static class KillPatch
    {
        private static void Postfix(MessageManager __instance, PersistentID killerID, PersistentID killedID, KillType killedType)
        {
            try
            {
                if (__instance.IsServer)
                    instance?.RecordKill(killerID, killedID, killedType);
            }
            catch (Exception error) { instance?.Warn(error); }
        }
    }

    [Serializable]
    private sealed class Report
    {
        public int schema = 1;
        public string id, mission, started_utc, ended_utc, updated_utc, game_instance, status;
        public List<PlayerResult> players = new List<PlayerResult>();
        public List<CombatWindow> timeline = new List<CombatWindow>();
    }

    [Serializable]
    private sealed class CombatWindow
    {
        public int minute, aircraft, surface, missiles;
    }

    [Serializable]
    private sealed class PlayerResult
    {
        public string steam_id, name;
        public int player_aircraft, ai_aircraft, buildings, vehicles, ships, missiles, assists, deaths;
    }
}
