// CS2 Coach - Teleport-Plugin fuer das Duell-Training.
//
// Ein einziger Server-Befehl, den der Coach per RCON aufruft:
//     coach_tp <userid> <x> <y> <z> <pitch> <yaw>
// Er setzt einen Spieler (Mensch oder Bot) mit Blickrichtung an eine Stelle.
// Alles andere - welche Situation, wann, wer - entscheidet der Coach
// (cs2_coach/training_server.py), damit dieses Plugin nach CS2-Updates
// so wenig wie moeglich anfassen muss.
//
// Warum ueberhaupt ein Plugin: am 30.10.2026 gemessen bewegt keiner der
// Konsolenbefehle (ent_setpos, setpos_player) einen echten Spieler - im
// Training stand dj nach dem "Versetzen" weiter am T-Spawn. Bots liessen
// sich mit ent_setpos nur eingefroren versetzen. Teleport() setzt Position,
// Blickrichtung und Geschwindigkeit so, wie es auch Trigger in Maps tun.

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
    public override string ModuleVersion => "1.0.0";
    public override string ModuleAuthor => "cs2-coach";

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
