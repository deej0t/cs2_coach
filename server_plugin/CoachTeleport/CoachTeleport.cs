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
//     coach_team <userid> <t|ct>
//         Mitspieler ins Team des Trainierenden setzen (Training zu mehreren).
//     coach_hunt <userid> <0|1>
//         Jagd: sobald der Bot nicht mehr seinen Weg laeuft (Uebergabe an die KI)
//         oder jemand in der Runde gestorben ist, kennt er die Position des
//         naechsten Gegners und geht dorthin ("Geraeusch" an der Stelle, alle
//         0,25 s erneuert). Der erste Tote der Runde beendet auch alle Wege.
//     coach_stats <userid>
//         Messwerte der laufenden Runde fuer einen Menschen (seit Freeze-Ende):
//         seen=ms bis zum ersten Sichtkontakt, start=1 wenn der Gegner schon
//         beim Freeze-Ende sichtbar war, err/dpitch/dyaw=Fadenkreuz-Abstand zum
//         Gegner in dem Moment (Grad, dpitch>0 = zu tief), react=ms vom Sehen
//         bis zum ersten Schuss (negativ = vorher geschossen), hit=ms bis zum
//         ersten Treffer, peek=ms, die der Gegner dich vorher sah (negativ = du
//         ihn zuerst), kills=Fadenkreuz 0,5 s vor jedem Kill (wie im Match-Parser).
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
using Microsoft.Extensions.Logging;
using System.Globalization;

namespace CoachTeleport;

public class CoachTeleport : BasePlugin
{
    public override string ModuleName => "CS2 Coach Teleport";
    public override string ModuleVersion => "1.10.0";
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
        RegisterListener<Listeners.OnTick>(AimAssist);
        RegisterListener<Listeners.OnTick>(Hunt);
        RegisterListener<Listeners.OnTick>(MeasureTick);
        RegisterEventHandler<EventWeaponFire>((ev, _) =>
        {
            if (_inFreeze || ev.Userid is not { } shooter || shooter.IsBot) return HookResult.Continue;
            if (IsNoGun(ev.Weapon)) return HookResult.Continue;
            var m = MeasureOf(shooter.UserId ?? -1);
            if (m.ShotTick < 0) m.ShotTick = Server.TickCount;
            return HookResult.Continue;
        });
        RegisterListener<Listeners.OnPlayerButtonsChanged>(OnButtons);
        RegisterEventHandler<EventRoundStart>((_, _) =>
        {
            _inFreeze = true;
            _sawAt.Clear();
            _roundKill = false;
            return HookResult.Continue;
        });
        RegisterEventHandler<EventRoundFreezeEnd>((_, _) =>
        {
            _inFreeze = false;
            _measures.Clear();
            _views.Clear();
            _freezeEndTick = Server.TickCount;
            foreach (var r in _replays.Values.Where(r => r.Armed && r.StartTick < 0))
                r.StartTick = Server.TickCount;
            return HookResult.Continue;
        });
        // Getroffen: ab jetzt reagiert die Bot-KI selbst.
        RegisterEventHandler<EventPlayerHurt>((ev, _) =>
        {
            if (ev.Userid is { } victim) _replays.Remove(victim.UserId ?? -1);
            if (!_inFreeze && ev.Attacker is { IsBot: false } att && ev.Userid is { } vic && vic.TeamNum != att.TeamNum)
            {
                var m = MeasureOf(att.UserId ?? -1);
                if (m.HitTick < 0) m.HitTick = Server.TickCount;
            }
            return HookResult.Continue;
        });
        RegisterEventHandler<EventPlayerDeath>((ev, _) =>
        {
            if (ev.Userid is { } victim) _replays.Remove(victim.UserId ?? -1);
            if (!_inFreeze) OnFirstKill();
            if (!_inFreeze && ev.Attacker is { IsBot: false } att && ev.Userid is { } dead
                && dead.TeamNum != att.TeamNum && ev.Penetrated == 0 && !IsNoGun(ev.Weapon))
                RecordKillError(att, dead);
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
    private static bool SeesEnemy(CCSPlayerController bot) => SeenEnemy(bot) is not null;

    private static CCSPlayerPawn? SeenEnemy(CCSPlayerController bot)
    {
        var slot = bot.Slot;
        if (slot < 0 || slot >= 64) return null;
        foreach (var p in Utilities.GetPlayers())
        {
            if (p == bot || p.TeamNum == bot.TeamNum) continue;
            var pawn = p.PlayerPawn.Value;
            if (pawn is null || !pawn.IsValid || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE) continue;
            var mask = pawn.EntitySpottedState.SpottedByMask;
            if ((mask[slot / 32] & (1u << (slot % 32))) != 0) return pawn;
        }
        return null;
    }

    // Zielhilfe fuer Bot-Stufe 4 und 5. Am 30.10. gewann der Spieler auch nach der
    // Uebergabe an die KI beim ersten Sichtkontakt 19 von 20 Duellen auf
    // bot_difficulty 3; der Bot traf nur zweimal zuerst. Die Valve-KI dreht sich
    // zu langsam. Hier: nach ReactTicks liegt das Fadenkreuz auf dem Kopf (Augenhoehe
    // aus ViewOffset, also auch geduckt richtig) und bleibt LockTicks dort.
    // Geschossen wird weiter von der KI.
    private readonly Dictionary<int, (int ReactTicks, int LockTicks)> _skills = new();
    private readonly Dictionary<int, int> _sawAt = new();

    private void AimAssist()
    {
        if (_skills.Count == 0) return;
        foreach (var (userid, skill) in _skills.ToList())
        {
            if (_replays.ContainsKey(userid)) continue;  // laeuft noch seinen Weg
            var bot = Utilities.GetPlayerFromUserid(userid);
            var pawn = bot?.PlayerPawn.Value;
            if (bot is null || !bot.IsValid || pawn is null || !pawn.IsValid
                || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE || pawn.AbsOrigin is null) continue;
            var target = SeenEnemy(bot);
            var now = Server.TickCount;
            if (target?.AbsOrigin is null)
            {
                _sawAt.Remove(userid);  // aus den Augen: naechster Sichtkontakt zaehlt neu
                continue;
            }
            if (!_sawAt.TryGetValue(userid, out var saw)) _sawAt[userid] = saw = now;
            var since = now - saw;
            if (since < skill.ReactTicks || since >= skill.ReactTicks + skill.LockTicks) continue;
            var from = pawn.AbsOrigin;
            var eye = pawn.ViewOffset.Z;
            var dx = target.AbsOrigin.X - from.X;
            var dy = target.AbsOrigin.Y - from.Y;
            var dz = target.AbsOrigin.Z + target.ViewOffset.Z - (from.Z + eye);
            var yaw = (float)(Math.Atan2(dy, dx) * 180 / Math.PI);
            var pitch = (float)(-Math.Atan2(dz, Math.Sqrt(dx * dx + dy * dy)) * 180 / Math.PI);
            pawn.Teleport(null, new QAngle(pitch, yaw, 0), null);
        }
    }

    [ConsoleCommand("coach_bot_skill", "Zielhilfe: <userid> <reaktion_ms> <halten_ms> (0 0 = aus)")]
    [CommandHelper(minArgs: 3, usage: "<userid> <reaktion_ms> <halten_ms>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnBotSkill(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid) || !int.TryParse(info.GetArg(2), out var react)
            || !int.TryParse(info.GetArg(3), out var hold))
        {
            info.ReplyToCommand("coach_bot_skill: ungueltige Argumente");
            return;
        }
        _sawAt.Remove(userid);
        if (hold <= 0) _skills.Remove(userid);
        else _skills[userid] = (Math.Max(0, react) * 64 / 1000, hold * 64 / 1000);
        info.ReplyToCommand($"coach_bot_skill ok {userid}");
    }

    // Messung (1.10.0). Bisher stand im Log nur, wer zuerst getroffen hat; die
    // Reaktionszeit und wo das Fadenkreuz war, als der Gegner auftauchte, fehlten
    // ("steht nicht im Log", training_server.duel_tip). Sichtkontakt = Spotted-
    // Maske (dieselben Radar-Daten wie bei der Uebergabe an die KI). Aufloesung
    // 1 Tick = 15,6 ms.
    private sealed class Measure
    {
        public int SeenTick = -1, EnemySawTick = -1, ShotTick = -1, HitTick = -1;
        public bool AtStart;
        public float Err = -1, DPitch, DYaw;
        public readonly List<float> KillErrs = new();
    }

    private readonly Dictionary<int, Measure> _measures = new();
    private readonly Dictionary<int, Queue<(int Tick, float X, float Y, float Z, float Pitch, float Yaw)>> _views = new();
    private int _freezeEndTick = -1;
    private const int PreAimTicks = 32;  // wie PRE_AIM_OFFSET_TICKS im Match-Parser

    private Measure MeasureOf(int userid)
    {
        if (!_measures.TryGetValue(userid, out var m)) _measures[userid] = m = new Measure();
        return m;
    }

    private static bool IsNoGun(string weapon)
    {
        var w = (weapon ?? "").ToLowerInvariant();
        return w.Contains("knife") || w.Contains("bayonet") || w.Contains("grenade") || w.Contains("flashbang")
            || w.Contains("molotov") || w.Contains("decoy") || w.Contains("inferno") || w.Contains("c4")
            || w.Contains("taser") || w.Length == 0;
    }

    // Wie _angular_error im Parser: Fuss zu Fuss, Yaw-Differenz mit Umlauf,
    // dpitch = eigener Pitch minus idealer Pitch (Source: positiv = nach unten).
    private static (float Err, float DPitch, float DYaw) AngularError(float fx, float fy, float fz, float pitch, float yaw, Vector to)
    {
        var dx = to.X - fx;
        var dy = to.Y - fy;
        var dz = to.Z - fz;
        var d2 = Math.Sqrt(dx * dx + dy * dy);
        if (d2 < 1) return (0, 0, 0);
        var idealYaw = Math.Atan2(dy, dx) * 180 / Math.PI;
        var idealPitch = -Math.Atan2(dz, d2) * 180 / Math.PI;
        var dyaw = ((yaw - idealYaw) % 360 + 540) % 360 - 180;
        var dpitch = pitch - idealPitch;
        return ((float)Math.Sqrt(dyaw * dyaw + dpitch * dpitch), (float)dpitch, (float)dyaw);
    }

    private static bool SeenByEnemy(CCSPlayerController human, CCSPlayerPawn pawn)
    {
        var mask = pawn.EntitySpottedState.SpottedByMask;
        foreach (var p in Utilities.GetPlayers())
        {
            if (p == human || p.TeamNum == human.TeamNum || p.TeamNum < 2) continue;
            var other = p.PlayerPawn.Value;
            if (other is null || !other.IsValid || other.LifeState != (byte)LifeState_t.LIFE_ALIVE) continue;
            var slot = p.Slot;
            if (slot >= 0 && slot < 64 && (mask[slot / 32] & (1u << (slot % 32))) != 0) return true;
        }
        return false;
    }

    private void MeasureTick()
    {
        if (_inFreeze || _freezeEndTick < 0) return;
        var now = Server.TickCount;
        foreach (var human in Utilities.GetPlayers())
        {
            if (human.IsBot || human.TeamNum < 2) continue;
            var pawn = human.PlayerPawn.Value;
            if (pawn is null || !pawn.IsValid || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE
                || pawn.AbsOrigin is null) continue;
            var uid = human.UserId ?? -1;
            var (x, y, z) = (pawn.AbsOrigin.X, pawn.AbsOrigin.Y, pawn.AbsOrigin.Z);
            var pitch = pawn.EyeAngles.X;
            var yaw = pawn.EyeAngles.Y;
            if (!_views.TryGetValue(uid, out var views)) _views[uid] = views = new();
            views.Enqueue((now, x, y, z, pitch, yaw));
            while (views.Count > PreAimTicks + 4) views.Dequeue();
            var m = MeasureOf(uid);
            if (m.EnemySawTick < 0 && SeenByEnemy(human, pawn)) m.EnemySawTick = now;
            if (m.SeenTick >= 0) continue;
            var foe = SeenEnemy(human);
            if (foe?.AbsOrigin is null) continue;
            m.SeenTick = now;
            m.AtStart = now - _freezeEndTick <= 2;
            (m.Err, m.DPitch, m.DYaw) = AngularError(x, y, z, pitch, yaw, foe.AbsOrigin);
        }
    }

    private void RecordKillError(CCSPlayerController att, CCSPlayerController dead)
    {
        var target = dead.PlayerPawn.Value?.AbsOrigin;
        if (target is null || !_views.TryGetValue(att.UserId ?? -1, out var views) || views.Count == 0) return;
        var want = Server.TickCount - PreAimTicks;
        var best = views.OrderBy(v => Math.Abs(v.Tick - want)).First();
        if (Math.Abs(best.Tick - want) > 4) return;  // Kill zu frueh nach Freeze-Ende: kein Blick von davor
        var (err, _, _) = AngularError(best.X, best.Y, best.Z, best.Pitch, best.Yaw, target);
        if (err <= 180) MeasureOf(att.UserId ?? -1).KillErrs.Add(err);
    }

    [ConsoleCommand("coach_stats", "Messwerte der Runde: <userid>")]
    [CommandHelper(minArgs: 1, usage: "<userid>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnStats(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid))
        {
            info.ReplyToCommand("coach_stats: ungueltige userid");
            return;
        }
        var ci = CultureInfo.InvariantCulture;
        string Ms(int a, int b) => a >= 0 && b >= 0 ? ((a - b) * 1000 / 64).ToString(ci) : "-";
        _measures.TryGetValue(userid, out var m);
        m ??= new Measure();
        var parts = new List<string>
        {
            $"seen={Ms(m.SeenTick, _freezeEndTick)}", $"start={(m.AtStart ? 1 : 0)}",
            $"err={(m.SeenTick >= 0 ? m.Err.ToString("F1", ci) : "-")}",
            $"dpitch={(m.SeenTick >= 0 ? m.DPitch.ToString("F1", ci) : "-")}",
            $"dyaw={(m.SeenTick >= 0 ? m.DYaw.ToString("F1", ci) : "-")}",
            $"react={Ms(m.ShotTick, m.SeenTick)}", $"hit={Ms(m.HitTick, m.SeenTick)}",
            $"peek={Ms(m.SeenTick, m.EnemySawTick)}",
            $"kills={(m.KillErrs.Count > 0 ? string.Join(",", m.KillErrs.Select(k => k.ToString("F1", ci))) : "-")}",
        };
        info.ReplyToCommand($"coach_stats {userid} " + string.Join(" ", parts));
    }

    // Jagd (1.9.0). Wunsch vom 30.10.: "die Bots duerfen gerne wissen wo ich bin,
    // das die auf mich zu kommen - spaetestens nach dem ersten Kill". Nach der
    // Uebergabe wusste die KI nicht, wo der Spieler ist, und lief irgendwohin.
    // Die Valve-KI geht Geraeuschen nach (InvestigateNoise); hier bekommt sie
    // alle 16 Ticks ein frisches "Geraeusch" an der Stelle des naechsten Gegners.
    // Ob die KI dem wirklich folgt, zeigt das Log: alle 2 s die Entfernung.
    private readonly HashSet<int> _hunters = new();
    private bool _roundKill;

    private void OnFirstKill()
    {
        if (_roundKill) return;
        _roundKill = true;
        foreach (var userid in _hunters)
        {
            if (!_replays.Remove(userid)) continue;
            var bot = Utilities.GetPlayerFromUserid(userid);
            var pawn = bot?.PlayerPawn.Value;
            if (bot is not null && bot.IsValid && pawn is not null && pawn.IsValid
                && pawn.LifeState == (byte)LifeState_t.LIFE_ALIVE) FaceNearestEnemy(bot, pawn);
        }
    }

    private void Hunt()
    {
        if (_hunters.Count == 0 || _inFreeze || Server.TickCount % 16 != 0) return;
        foreach (var userid in _hunters.ToList())
        {
            if (_replays.ContainsKey(userid)) continue;  // laeuft noch seinen echten Weg
            var bot = Utilities.GetPlayerFromUserid(userid);
            var pawn = bot?.PlayerPawn.Value;
            if (bot is null || !bot.IsValid || pawn is null || !pawn.IsValid
                || pawn.LifeState != (byte)LifeState_t.LIFE_ALIVE || pawn.AbsOrigin is null) continue;
            if (SeesEnemy(bot)) continue;  // kaempft schon - nicht stoeren
            var target = NearestEnemy(bot, pawn);
            var brain = pawn.Bot;
            if (target?.AbsOrigin is null || brain is null) continue;
            var dist = (target.AbsOrigin - pawn.AbsOrigin).Length();
            brain.NoisePosition.X = target.AbsOrigin.X;
            brain.NoisePosition.Y = target.AbsOrigin.Y;
            brain.NoisePosition.Z = target.AbsOrigin.Z;
            brain.NoiseTravelDistance = dist;
            brain.NoiseTimestamp = Server.CurrentTime - 0.5f;  // Reaktionszeit schon vorbei
            if (Server.TickCount % 128 == 0)
                Logger.LogInformation("coach_hunt {Bot} dist {Dist:F0}", userid, dist);
        }
    }

    [ConsoleCommand("coach_hunt", "Bot kennt die Position des naechsten Gegners: <userid> <0|1>")]
    [CommandHelper(minArgs: 2, usage: "<userid> <0|1>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnHunt(CCSPlayerController? caller, CommandInfo info)
    {
        if (!int.TryParse(info.GetArg(1), out var userid))
        {
            info.ReplyToCommand("coach_hunt: ungueltige userid");
            return;
        }
        if (info.GetArg(2) == "0") _hunters.Remove(userid);
        else _hunters.Add(userid);
        info.ReplyToCommand($"coach_hunt ok {userid}");
    }

    private static CCSPlayerPawn? NearestEnemy(CCSPlayerController bot, CCSPlayerPawn pawn)
    {
        var from = pawn.AbsOrigin;
        if (from is null) return null;
        CCSPlayerPawn? best = null;
        var bestDist = float.MaxValue;
        foreach (var p in Utilities.GetPlayers())
        {
            var other = p.PlayerPawn.Value;
            if (p == bot || p.TeamNum == bot.TeamNum || p.TeamNum < 2 || other is null || !other.IsValid
                || other.LifeState != (byte)LifeState_t.LIFE_ALIVE || other.AbsOrigin is null) continue;
            var d = (other.AbsOrigin - from).Length();
            if (d < bestDist) { bestDist = d; best = other; }
        }
        return best;
    }

    private static void FaceNearestEnemy(CCSPlayerController bot, CCSPlayerPawn pawn)
    {
        var from = pawn.AbsOrigin;
        var best = NearestEnemy(bot, pawn);
        if (from is null || best?.AbsOrigin is null) return;
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

    [ConsoleCommand("coach_team", "Mitspieler ins Team des Trainierenden setzen: <userid> <t|ct>")]
    [CommandHelper(minArgs: 2, usage: "<userid> <t|ct>", whoCanExecute: CommandUsage.SERVER_ONLY)]
    public void OnTeam(CCSPlayerController? caller, CommandInfo info)
    {
        var player = int.TryParse(info.GetArg(1), out var userid) ? Utilities.GetPlayerFromUserid(userid) : null;
        if (player is null || !player.IsValid)
        {
            info.ReplyToCommand("coach_team: kein Spieler");
            return;
        }
        var team = info.GetArg(2).ToLowerInvariant() == "ct" ? CsTeam.CounterTerrorist : CsTeam.Terrorist;
        if ((CsTeam)player.TeamNum != team) player.ChangeTeam(team);
        info.ReplyToCommand($"coach_team ok {userid}");
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
