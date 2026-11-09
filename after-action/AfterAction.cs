using System;
using System.Collections.Generic;
using System.IO;
using BepInEx;
using HarmonyLib;
using NuclearOption.DedicatedServer;
using NuclearOption.Networking;
using NuclearOption.SavedMission;
using UnityEngine;

[BepInPlugin(PluginId, "After-action reports", "0.1.0")]
public sealed class AfterAction : BaseUnityPlugin
{
    private const string PluginId = "io.github.alexstapleton.nuclearoption.afteraction";
    private static AfterAction instance;
    private Harmony harmony;
    private string directory;
    private Report report;
    private bool warned;
    private readonly Dictionary<ulong, PlayerResult> players = new Dictionary<ulong, PlayerResult>();
    private readonly Dictionary<PersistentID, HashSet<ulong>> assists = new Dictionary<PersistentID, HashSet<ulong>>();

    private void Awake()
    {
        directory = Environment.GetEnvironmentVariable("NO_AAR_REPORT_DIR");
        if (string.IsNullOrEmpty(directory) || Array.IndexOf(Environment.GetCommandLineArgs(), "-DedicatedServer") < 0)
            return;

        try
        {
            Directory.CreateDirectory(directory);
            harmony = new Harmony(PluginId);
            harmony.PatchAll(typeof(AfterAction).Assembly);
            instance = this;
            Logger.LogInfo("After-action collector ready.");
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
            started_utc = DateTime.UtcNow.ToString("O")
        };
        foreach (var player in UnitRegistry.playerLookup.Values)
            GetPlayer(player);
    }

    private void Finish(string status)
    {
        if (report == null)
            return;

        try
        {
            report.ended_utc = DateTime.UtcNow.ToString("O");
            report.status = status;
            report.players.AddRange(players.Values);
            string file = Path.Combine(directory, report.id + ".json");
            string temporary = file + ".tmp";
            File.WriteAllText(temporary, JsonUtility.ToJson(report));
            File.Move(temporary, file);
        }
        catch (Exception error)
        {
            Logger.LogWarning($"Could not save the after-action report: {error.Message}");
        }
        finally
        {
            report = null;
            players.Clear();
            assists.Clear();
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
            switch (killedType)
            {
                case KillType.Aircraft:
                    if (pilot != null) killer.player_aircraft++;
                    else killer.ai_aircraft++;
                    break;
                case KillType.Building: killer.buildings++; break;
                case KillType.Vehicle: killer.vehicles++; break;
                case KillType.Ship: killer.ships++; break;
                case KillType.Missile: killer.missiles++; break;
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
        public string id, mission, started_utc, ended_utc, status;
        public List<PlayerResult> players = new List<PlayerResult>();
    }

    [Serializable]
    private sealed class PlayerResult
    {
        public string steam_id, name;
        public int player_aircraft, ai_aircraft, buildings, vehicles, ships, missiles, assists, deaths;
    }
}
