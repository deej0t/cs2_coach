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
//     coach_hud <userid> <sekunden> <html_base64>
//         Grosse Einblendung in der Bildschirmmitte (HTML, Base64, damit
//         Leerzeichen, Anfuehrungszeichen und Semikolons die Konsole nicht stoeren).
//     coach_menu <userid> <json_base64>
//         Menue {"title": "...", "items": [["Text", "!befehl"], ...]}: W/S waehlen,
//         E bestaetigt. Die Auswahl schreibt der Spieler als Chatbefehl
//         ("say !befehl") - so kommt sie ueber das Server-Log beim Coach an.
//     coach_chat <userid> <text_base64>
//         Private Chatzeilen nur fuer diesen Spieler (KI-Coach, Tipps). Zeilen
//         werden am Zeilenumbruch getrennt.
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
    public override string ModuleVersion => "1.6.1";
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

    private sealed class Menu
    {
        public string Title = "";
        public List<(string Label, string Command)> Items = new();
        public int Cursor;
    }

    private readonly Dictionary<int, (string Html, int UntilTick)> _huds = new();
    private readonly Dictionary<int, Menu> _menus = new();

    public override void Load(bool hotReload)
    {
        RegisterListener<Listeners.OnTick>(OnTick);
        RegisterListener<Listeners.OnTick>(DrawCenter);
        RegisterListener<Listeners.OnPlayerButtonsChanged>(OnButtons);
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
            // Sieht der Bot seinen Gegner, uebernimmt sofort die KI. Waehrend des Wegs
            // setzt das Plugin jeden Tick auch die Blickrichtung - die KI kann dann
            // nicht zielen. Am 30.10. gewann der Spieler so 15 von 15 Duellen, in 13
            // davon traf ihn der Bot kein einziges Mal ("noch zu schwach").
            if (SeesEnemy(player))
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

    // Einblendung und Menue teilen sich die Bildschirmmitte; das Menue hat Vorrang.
    //
    // JEDEN Tick senden - genau wie CounterStrikeSharps eigenes CenterHtmlMenu
    // (OnTick -> Display -> PrintToCenterHtml). Zwei Fehlversuche am 30.10.2026:
    // viermal pro Sekunde (1.3.0/1.4.0) "blinkt zu sehr, dass man kaum was lesen
    // kann"; einmal mit langer Dauer (1.4.1-1.6.0) "wird nur sehr kurz angezeigt" -
    // CS2 haelt die Einblendung nicht ueber die angegebene Dauer.
    private readonly Dictionary<int, (string Html, int UntilTick)> _shown = new();

    private void DrawCenter()
    {
        if (_huds.Count == 0 && _menus.Count == 0 && _shown.Count == 0) return;
        foreach (var userid in _huds.Keys.Concat(_menus.Keys).Concat(_shown.Keys).Distinct().ToList())
            Refresh(userid);
    }

    private void Refresh(int userid)
    {
        var player = Utilities.GetPlayerFromUserid(userid);
        if (player is null || !player.IsValid)
        {
            _huds.Remove(userid);
            _menus.Remove(userid);
            _shown.Remove(userid);
            return;
        }
        var now = Server.TickCount;
        if (_huds.TryGetValue(userid, out var expired) && now > expired.UntilTick) _huds.Remove(userid);

        string? html = null;
        if (_menus.TryGetValue(userid, out var menu)) html = RenderMenu(menu);
        else if (_huds.TryGetValue(userid, out var hud)) html = hud.Html;

        if (html is null)
        {
            // Nichts mehr anzuzeigen: einmal leeren, damit nichts nachhaengt.
            if (_shown.ContainsKey(userid)) TryCenter(player, " ", 1);
            _shown.Remove(userid);
            return;
        }
        if (!TryCenter(player, html, 1))
        {
            _huds.Remove(userid);
            _menus.Remove(userid);
            _shown.Remove(userid);
            return;
        }
        _shown[userid] = (html, now + 64);
    }

    // PrintToCenterHtml wirft "Could not get player event listener" fuer Bots und
    // Spieler ohne Verbindung (gesehen am 30.10. mit einer ungueltigen userid) -
    // dann verwerfen statt zweimal pro Sekunde erneut zu scheitern.
    private static bool TryCenter(CCSPlayerController player, string html, int seconds)
    {
        if (player.IsBot) return false;
        try
        {
            player.PrintToCenterHtml(html, seconds);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static string RenderMenu(Menu m)
    {
        var lines = new List<string> { $"<font class='fontSize-m' color='#9184d9'>{m.Title}</font>" };
        for (var i = 0; i < m.Items.Count; i++)
        {
            lines.Add(i == m.Cursor
                ? $"<font color='#4ade80'>&#9658; {m.Items[i].Label}</font>"
                : $"<font color='#cccccc'>{m.Items[i].Label}</font>");
        }
        lines.Add("<font class='fontSize-s' color='#888888'>W/S w&#228;hlen &#183; E best&#228;tigen</font>");
        return string.Join("<br>", lines);
    }

    private void OnButtons(CCSPlayerController player, PlayerButtons pressed, PlayerButtons released)
    {
        if (player.UserId is not { } userid || !_menus.TryGetValue(userid, out var m) || m.Items.Count == 0) return;
        if ((pressed & PlayerButtons.Forward) != 0) m.Cursor = (m.Cursor + m.Items.Count - 1) % m.Items.Count;
        else if ((pressed & PlayerButtons.Back) != 0) m.Cursor = (m.Cursor + 1) % m.Items.Count;
        else if ((pressed & PlayerButtons.Use) != 0)
        {
            var command = m.Items[m.Cursor].Command;
            _menus.Remove(userid);
            Refresh(userid);
            if (!string.IsNullOrWhiteSpace(command)) player.ExecuteClientCommandFromServer($"say {command}");
            return;
        }
        else return;
        Refresh(userid);
    }

    private static string? FromBase64(string value)
    {
        try { return System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(value)); }
        catch (FormatException) { return null; }
    }

    [ConsoleCommand("coach_hud", "Einblendung: <userid> <sekunden> <html_base64>")]
    [CommandHelper(minArgs: 3, usage: "<userid> <sekunden> <html_base64>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnHud(CCSPlayerController? caller, CommandInfo info)
    {
        var html = FromBase64(info.GetArg(3));
        if (!int.TryParse(info.GetArg(1), out var userid) || !float.TryParse(info.GetArg(2),
                NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds) || html is null)
        {
            info.ReplyToCommand("coach_hud: ungueltige Argumente");
            return;
        }
        if (seconds <= 0) _huds.Remove(userid);
        else _huds[userid] = (html, Server.TickCount + (int)(seconds * 64));
        Refresh(userid);
        info.ReplyToCommand("coach_hud ok");
    }

    [ConsoleCommand("coach_menu", "Menue: <userid> <json_base64> (leer = schliessen)")]
    [CommandHelper(minArgs: 1, usage: "<userid> <json_base64>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnMenu(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid))
        {
            info.ReplyToCommand("coach_menu: ungueltige userid");
            return;
        }
        var json = info.ArgCount > 2 ? FromBase64(info.GetArg(2)) : null;
        if (string.IsNullOrEmpty(json))
        {
            _menus.Remove(userid);
            Refresh(userid);
            info.ReplyToCommand("coach_menu ok closed");
            return;
        }
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var menu = new Menu { Title = doc.RootElement.GetProperty("title").GetString() ?? "" };
            foreach (var item in doc.RootElement.GetProperty("items").EnumerateArray())
                menu.Items.Add((item[0].GetString() ?? "", item[1].GetString() ?? ""));
            _menus[userid] = menu;
            Refresh(userid);
            info.ReplyToCommand($"coach_menu ok {menu.Items.Count}");
        }
        catch (Exception e) when (e is System.Text.Json.JsonException or KeyNotFoundException or InvalidOperationException)
        {
            info.ReplyToCommand($"coach_menu: ungueltiges JSON ({e.GetType().Name})");
        }
    }

    // Dieselben Daten wie fuers Radar: m_entitySpottedState.m_bSpottedByMask eines
    // Spielers hat ein Bit fuer jeden Slot, der ihn gerade sieht.
    private static bool SeesEnemy(CCSPlayerController bot)
    {
        var slot = bot.Slot;
        if (slot < 0 || slot >= 64) return false;
        foreach (var p in Utilities.GetPlayers())
        {
            if (p == bot || p.TeamNum == bot.TeamNum) continue;
            var pawn = p.PlayerPawn.Value;
            if (pawn is null || !pawn.IsValid || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE) continue;
            var mask = pawn.EntitySpottedState.SpottedByMask;
            if ((mask[slot / 32] & (1u << (slot % 32))) != 0) return true;
        }
        return false;
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

    [ConsoleCommand("coach_chat", "Private Chatzeilen: <userid> <text_base64>")]
    [CommandHelper(minArgs: 2, usage: "<userid> <text_base64>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnChat(CCSPlayerController? caller, CommandInfo info)
    {
        var text = FromBase64(info.GetArg(2));
        if (!int.TryParse(info.GetArg(1), out var userid) || text is null)
        {
            info.ReplyToCommand("coach_chat: ungueltige Argumente");
            return;
        }
        var player = Utilities.GetPlayerFromUserid(userid);
        if (player is null || !player.IsValid)
        {
            info.ReplyToCommand($"coach_chat: kein Spieler mit userid {userid}");
            return;
        }
        foreach (var line in text.Split('\n'))
        {
            // Fuehrendes Leerzeichen: sonst ignoriert CS2 die erste Farbe.
            if (!string.IsNullOrWhiteSpace(line))
                player.PrintToChat($" {ChatColors.Purple}[Coach]{ChatColors.Default} {line}");
        }
        info.ReplyToCommand("coach_chat ok");
    }

    [ConsoleCommand("coach_slay", "Bot aus der Runde nehmen (Szenario mit weniger Gegnern): <userid>")]
    [CommandHelper(minArgs: 1, usage: "<userid>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnSlay(CCSPlayerController? caller, CommandInfo info)
    {
        var player = int.TryParse(info.GetArg(1), out var userid) ? Utilities.GetPlayerFromUserid(userid) : null;
        var pawn = player?.PlayerPawn.Value;
        if (player is null || !player.IsValid || pawn is null || !pawn.IsValid)
        {
            info.ReplyToCommand("coach_slay: kein Spieler");
            return;
        }
        if (pawn.LifeState == (byte)LifeState_t.LIFE_ALIVE) pawn.CommitSuicide(false, true);
        info.ReplyToCommand($"coach_slay ok {userid}");
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
