// CS2 Coach - Plugin fuer das Duell-Training.
//
// Server-Befehle, die der Coach per RCON aufruft (cs2_coach/training_server.py):
//     coach_tp <userid> <x> <y> <z> <pitch> <yaw>
//         Spieler (Mensch oder Bot) mit Blickrichtung an eine Stelle setzen.
//     coach_path_clear <userid>
//     coach_path_add <userid> <x y z pitch yaw> [<x y z pitch yaw> ...]
//     coach_path_start <userid> <punkte_pro_sekunde>
//         Einen Bot den echten Weg des Gegners aus der Demo ablaufen lassen.
//         Startet mit dem Ende der Freeze-Time (oder sofort, wenn sie vorbei
//         ist), Tick fuer Tick zwischen den Punkten interpoliert. Wird der Bot
//         getroffen oder ist der Weg zu Ende, uebernimmt die Bot-KI.
//     coach_give <userid> <aktive_waffe> <item> [<item> ...]
//         Alles wegnehmen und genau diese Items geben (z. B. das Inventar aus
//         der Demo), danach die aktive Waffe in die Hand.
//     coach_tp_version
//
// Alles andere - welche Situation, wann, wer - entscheidet der Coach, damit
// dieses Plugin nach CS2-Updates so wenig wie moeglich anfassen muss.
//
// Warum ueberhaupt ein Plugin: am 30.10.2026 gemessen bewegt keiner der
// Konsolenbefehle (ent_setpos, setpos_player) einen echten Spieler. Und die
// Bot-KI laeuft nach dem Loslassen ihren eigenen Weg - "der bot laeuft den
// weg nicht wirklich ab und ist mega bottig".

using CounterStrikeSharp.API;
using CounterStrikeSharp.API.Core;
using CounterStrikeSharp.API.Core.Attributes.Registration;
using CounterStrikeSharp.API.Modules.Commands;
using CounterStrikeSharp.API.Modules.Utils;
using System.Globalization;

namespace CoachTeleport;

public class CoachTeleport : BasePlugin
{
    public override string ModuleName => "CS2 Coach Teleport";
    public override string ModuleVersion => "1.2.0";
    public override string ModuleAuthor => "cs2-coach";

    private sealed class Replay
    {
        public readonly List<float[]> Points = new();
        public float Hz = 16;
        public bool Armed;
        public int StartTick = -1;
    }

    private readonly Dictionary<int, Replay> _replays = new();
    private bool _inFreeze;

    public override void Load(bool hotReload)
    {
        RegisterListener<Listeners.OnTick>(OnTick);
        RegisterEventHandler<EventRoundStart>((_, _) =>
        {
            _inFreeze = true;
            return HookResult.Continue;
        });
        RegisterEventHandler<EventRoundFreezeEnd>((_, _) =>
        {
            _inFreeze = false;
            foreach (var r in _replays.Values.Where(r => r.Armed && r.StartTick < 0))
                r.StartTick = Server.TickCount;
            return HookResult.Continue;
        });
        // Getroffen: ab jetzt reagiert die Bot-KI selbst.
        RegisterEventHandler<EventPlayerHurt>((ev, _) =>
        {
            if (ev.Userid is { } victim) _replays.Remove(victim.UserId ?? -1);
            return HookResult.Continue;
        });
        RegisterEventHandler<EventPlayerDeath>((ev, _) =>
        {
            if (ev.Userid is { } victim) _replays.Remove(victim.UserId ?? -1);
            return HookResult.Continue;
        });
        RegisterEventHandler<EventRoundEnd>((_, _) =>
        {
            _replays.Clear();
            return HookResult.Continue;
        });
    }

    private void OnTick()
    {
        if (_replays.Count == 0) return;
        foreach (var (userid, r) in _replays.ToList())
        {
            if (r.StartTick < 0 || r.Points.Count < 2) continue;
            var player = Utilities.GetPlayerFromUserid(userid);
            var pawn = player?.PlayerPawn.Value;
            if (player is null || !player.IsValid || pawn is null || !pawn.IsValid || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE)
            {
                _replays.Remove(userid);
                continue;
            }
            var t = (Server.TickCount - r.StartTick) / 64f * r.Hz;
            var i = (int)t;
            if (i >= r.Points.Count - 1)
            {
                // Weg zu Ende: zum Gegner drehen, dann uebernimmt die Bot-KI. Ohne das
                // schaute der Bot dorthin, wo der Spieler in der Demo stand, und lief los,
                // ohne zu wissen, wo er wirklich ist.
                _replays.Remove(userid);
                FaceNearestEnemy(player, pawn);
                continue;
            }
            var f = t - i;
            var a = r.Points[i];
            var b = r.Points[i + 1];
            var pos = new Vector(Lerp(a[0], b[0], f), Lerp(a[1], b[1], f), Lerp(a[2], b[2], f));
            var ang = new QAngle(Lerp(a[3], b[3], f), LerpAngle(a[4], b[4], f), 0);
            // Geschwindigkeit wie im Original, damit die Laufanimation passt.
            var vel = new Vector((b[0] - a[0]) * r.Hz, (b[1] - a[1]) * r.Hz, 0);
            pawn.Teleport(pos, ang, vel);
        }
    }

    private static void FaceNearestEnemy(CCSPlayerController bot, CCSPlayerPawn pawn)
    {
        var from = pawn.AbsOrigin;
        if (from is null) return;
        CCSPlayerPawn? best = null;
        var bestDist = float.MaxValue;
        foreach (var p in Utilities.GetPlayers())
        {
            var other = p.PlayerPawn.Value;
            if (p == bot || p.TeamNum == bot.TeamNum || other is null || !other.IsValid
                || other.LifeState != (byte)LifeState_t.LIFE_ALIVE || other.AbsOrigin is null) continue;
            var d = (other.AbsOrigin - from).Length();
            if (d < bestDist) { bestDist = d; best = other; }
        }
        if (best?.AbsOrigin is null) return;
        var dx = best.AbsOrigin.X - from.X;
        var dy = best.AbsOrigin.Y - from.Y;
        var dz = best.AbsOrigin.Z - from.Z;
        var yaw = (float)(Math.Atan2(dy, dx) * 180 / Math.PI);
        var pitch = (float)(-Math.Atan2(dz, Math.Sqrt(dx * dx + dy * dy)) * 180 / Math.PI);
        pawn.Teleport(null, new QAngle(pitch, yaw, 0), null);
    }

    private static float Lerp(float a, float b, float f) => a + (b - a) * f;

    private static float LerpAngle(float a, float b, float f)
    {
        var d = ((b - a) % 360 + 540) % 360 - 180;  // kuerzester Weg um 180/-180
        return a + d * f;
    }

    [ConsoleCommand("coach_tp", "Spieler mit Blickrichtung versetzen: <userid> <x> <y> <z> <pitch> <yaw>")]
    [CommandHelper(minArgs: 6, usage: "<userid> <x> <y> <z> <pitch> <yaw>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnTeleport(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid) || !TryFloats(info, 2, 5, out var v))
        {
            info.ReplyToCommand("coach_tp: ungueltige Argumente");
            return;
        }
        var player = Utilities.GetPlayerFromUserid(userid);
        var pawn = player?.PlayerPawn.Value;
        if (player is null || !player.IsValid || pawn is null || !pawn.IsValid)
        {
            info.ReplyToCommand($"coach_tp: kein Spieler mit userid {userid}");
            return;
        }
        pawn.Teleport(new Vector(v[0], v[1], v[2]), new QAngle(v[3], v[4], 0), new Vector(0, 0, 0));
        info.ReplyToCommand($"coach_tp ok {userid}");
    }

    [ConsoleCommand("coach_path_clear", "Weg eines Bots verwerfen: <userid>")]
    [CommandHelper(minArgs: 1, usage: "<userid>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnPathClear(CCSPlayerController? caller, CommandInfo info)
    {
        if (int.TryParse(info.GetArg(1), out var userid)) _replays[userid] = new Replay();
        info.ReplyToCommand("coach_path ok");
    }

    [ConsoleCommand("coach_path_add", "Wegpunkte anhaengen: <userid> <x y z pitch yaw>...")]
    [CommandHelper(minArgs: 6, usage: "<userid> <x y z pitch yaw>...", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnPathAdd(CCSPlayerController? caller, CommandInfo info)
    {
        var n = info.ArgCount - 2;
        if (!int.TryParse(info.GetArg(1), out var userid) || n % 5 != 0 || !TryFloats(info, 2, n, out var v))
        {
            info.ReplyToCommand("coach_path: ungueltige Argumente");
            return;
        }
        if (!_replays.TryGetValue(userid, out var r)) _replays[userid] = r = new Replay();
        for (var i = 0; i < n; i += 5) r.Points.Add(v[i..(i + 5)]);
        info.ReplyToCommand($"coach_path ok {r.Points.Count}");
    }

    [ConsoleCommand("coach_path_start", "Weg ablaufen (ab Ende der Freeze-Time): <userid> <punkte_pro_sekunde>")]
    [CommandHelper(minArgs: 2, usage: "<userid> <punkte_pro_sekunde>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnPathStart(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid) || !_replays.TryGetValue(userid, out var r)
            || !float.TryParse(info.GetArg(2), NumberStyles.Float, CultureInfo.InvariantCulture, out var hz) || hz <= 0)
        {
            info.ReplyToCommand("coach_path: kein Weg oder ungueltige Rate");
            return;
        }
        r.Hz = hz;
        r.Armed = true;
        r.StartTick = _inFreeze ? -1 : Server.TickCount;
        info.ReplyToCommand($"coach_path ok start {r.Points.Count}");
    }

    [ConsoleCommand("coach_give", "Inventar setzen: <userid> <aktive_waffe> <item>...")]
    [CommandHelper(minArgs: 3, usage: "<userid> <aktive_waffe> <item>...", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnGive(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid))
        {
            info.ReplyToCommand("coach_give: ungueltige userid");
            return;
        }
        var player = Utilities.GetPlayerFromUserid(userid);
        if (player is null || !player.IsValid || player.PlayerPawn.Value is null)
        {
            info.ReplyToCommand($"coach_give: kein Spieler mit userid {userid}");
            return;
        }
        player.RemoveWeapons();
        var given = 0;
        for (var i = 3; i < info.ArgCount; i++)
        {
            var item = info.GetArg(i);
            if (!item.StartsWith("weapon_") && !item.StartsWith("item_")) continue;
            player.GiveNamedItem(item);
            given++;
        }
        var active = info.GetArg(2);
        if (active.StartsWith("weapon_")) player.ExecuteClientCommandFromServer($"use {active}");
        info.ReplyToCommand($"coach_give ok {userid} {given}");
    }

    [ConsoleCommand("coach_tp_version", "Version des Coach-Plugins (Coach prueft damit, ob es geladen ist)")]
    [CommandHelper(whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnVersion(CCSPlayerController? caller, CommandInfo info)
    {
        info.ReplyToCommand($"coach_tp {ModuleVersion}");
    }

    private static bool TryFloats(CommandInfo info, int first, int count, out float[] values)
    {
        values = new float[count];
        for (var i = 0; i < count; i++)
        {
            if (!float.TryParse(info.GetArg(first + i), NumberStyles.Float, CultureInfo.InvariantCulture, out values[i]))
                return false;
        }
        return true;
    }
}
