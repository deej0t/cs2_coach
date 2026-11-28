import pytest
import yaml

from cs2_coach import users


@pytest.fixture
def base(tmp_path):
    cp = tmp_path / "config.yaml"
    cp.write_text(yaml.dump({"steam_id": "76561190000000001", "gemini_api_key": "ADMIN-SECRET",
                             "demo_folder": "/demos", "server_rcon_host": "x"}), encoding="utf-8")
    return cp


def test_add_user_and_verify(base):
    users.add_user(base, "Mate_1", "geheim1234", "76561190000000002", "Mate")
    assert users.verify(base, "mate_1", "geheim1234") and not users.verify(base, "mate_1", "falsch")
    with pytest.raises(ValueError):
        users.add_user(base, "admin", "geheim1234", "76561190000000002")
    with pytest.raises(ValueError):
        users.add_user(base, "x2", "kurz", "76561190000000002")
    with pytest.raises(ValueError):
        users.add_user(base, "x2", "geheim1234", "123")


def test_merged_config_hides_admin_keys_and_has_own_paths(base):
    users.add_user(base, "mate", "geheim1234", "76561190000000002")
    admin = yaml.safe_load(base.read_text(encoding="utf-8"))
    cfg = users.merged_config(admin, base, "mate")
    assert cfg["steam_id"] == "76561190000000002" and "gemini_api_key" not in cfg
    assert cfg["demo_folder"] == "/demos" and cfg["server_rcon_host"] == "x"
    assert cfg["obsidian_vault_path"].endswith(str(base.parent.name and "vault"))
    # Mate speichert nur eigene Schluessel - nie welche der Admin-Config
    users.save_overlay(base, "mate", cfg | {"gemini_api_key": "MATE-KEY", "server_rcon_host": "hack"})
    over = users.load_overlay(base, "mate")
    assert over["gemini_api_key"] == "MATE-KEY" and "server_rcon_host" not in over and "demo_folder" not in over


def test_shared_dir_and_active_user(base):
    up = users.user_config_path(base, "mate")
    assert users.shared_dir(up) == base.parent and users.shared_dir(base) == base.parent
    assert users.ACTIVE.get() is None
    with users.as_user("mate"):
        assert users.ACTIVE.get() == "mate"
    assert users.ACTIVE.get() is None


def test_steam_session_per_user(base, monkeypatch):
    from cs2_coach import sharecode
    monkeypatch.setenv("CS2COACH_CONFIG", str(base))
    assert sharecode._session_file() == base.parent / ".steam_session"
    with users.as_user("mate"):
        assert sharecode._session_file() == base.parent / "users" / "mate" / ".steam_session"


def test_views_of_lists_players_with_export(base):
    users.add_user(base, "mate", "geheim1234", "76561190000000002")
    admin = yaml.safe_load(base.read_text(encoding="utf-8"))
    admin["obsidian_vault_path"] = str(base.parent / "adminvault")
    for cfg in (admin, users.merged_config(admin, base, "mate")):
        d = users.export_dir(cfg)
        d.mkdir(parents=True)
        (d / f"x_{cfg.get('user', 'admin')}_coach.json").write_text('{"match": {"demo_file": "m1.dem"}}', encoding="utf-8")
    views = users.views_of(admin, base, "m1.dem")
    assert [v["user"] for v in views] == [None, "mate"] and views[1]["file"] == "x_mate_coach.json"


def test_register_pending_then_accept(base):
    users.request_access(base, "Neu", "geheim1234", "76561190000000003", "Neuer")
    assert "neu" in users.load_pending(base) and not users.verify(base, "neu", "geheim1234")
    with pytest.raises(ValueError):
        users.request_access(base, "neu", "geheim1234", "76561190000000003")
    users.decide(base, "neu", True)
    assert users.verify(base, "neu", "geheim1234") and not users.load_pending(base)
    assert users.load_overlay(base, "neu")["steam_id"] == "76561190000000003"
    users.request_access(base, "weg", "geheim1234", "76561190000000004")
    users.decide(base, "weg", False)
    assert "weg" not in users.load_users(base) and not users.load_pending(base)


def test_steam_openid_verify():
    from cs2_coach.web import steam_openid as so

    class R:
        def __init__(self, t): self.text = t
    rt = "https://x.de/steam-anmelden/zurueck"
    args = {"openid.mode": "id_res", "openid.return_to": rt + "?x=1",
            "openid.claimed_id": "https://steamcommunity.com/openid/id/76561190000000007"}
    assert so.verify(args, rt, post=lambda *a, **k: R("ns:x\nis_valid:true\n")) == "76561190000000007"
    assert so.verify(args, rt, post=lambda *a, **k: R("is_valid:false")) is None
    assert so.verify(args | {"openid.return_to": "https://boese.de/"}, rt, post=lambda *a, **k: R("is_valid:true")) is None
    assert so.verify(args | {"openid.claimed_id": "https://evil/7656119"}, rt, post=lambda *a, **k: R("is_valid:true")) is None


def test_steam_only_registration_has_no_password_login(base):
    users.request_access(base, "steam000007", None, "76561190000000007")
    users.decide(base, "steam000007", True)
    assert not users.verify(base, "steam000007", "") and not users.verify(base, "steam000007", "irgendwas1")


def test_steam_id_cannot_be_claimed_twice(base):
    admin_sid = str(__import__("yaml").safe_load(base.read_text(encoding="utf-8")).get("steam_id") or "")
    users.request_access(base, "erster", "geheim1234", "76561190000000011")
    assert users.load_pending(base)["erster"]["verified"] is False
    with pytest.raises(ValueError, match="Steam-ID"):
        users.request_access(base, "zweiter", "geheim1234", "76561190000000011")
    if admin_sid:
        with pytest.raises(ValueError, match="Steam-ID"):
            users.request_access(base, "dritter", "geheim1234", admin_sid)
    users.decide(base, "erster", True)
    with pytest.raises(ValueError, match="Steam-ID"):
        users.add_user(base, "vierter", "geheim1234", "76561190000000011")
    users.add_user(base, "erster", "", "76561190000000011")      # eigene ID behalten geht
    users.request_access(base, "steam000012", None, "76561190000000012")
    assert users.load_pending(base)["steam000012"]["verified"] is True


def test_duo_table_compares_together_vs_alone(base):
    from cs2_coach import duo
    admin = yaml.safe_load(base.read_text(encoding="utf-8"))
    admin["obsidian_vault_path"] = str(base.parent / "vault")
    users.add_user(base, "mate", "geheim1234", "76561190000000002")
    a_dir = users.export_dir(admin)
    m_dir = users.export_dir(users.merged_config(admin, base, "mate"))
    a_dir.mkdir(parents=True)
    m_dir.mkdir(parents=True)
    for i in range(20):
        res = "Sieg" if (i < 10 and i % 5) or (i >= 10 and i % 5 == 0) else "Niederlage"
        (a_dir / f"m{i}_coach.json").write_text(
            f'{{"match": {{"map": "de_mirage", "result": "{res}", "demo_file": "m{i}.dem"}}}}', encoding="utf-8")
        if i < 10:
            (m_dir / f"m{i}_coach.json").write_text(f'{{"match": {{"demo_file": "m{i}.dem"}}}}', encoding="utf-8")
    rows = duo.duo_table(admin, base, None)
    assert len(rows) == 1 and rows[0]["user"] == "mate"
    assert rows[0]["together"] == {"n": 10, "wins": 8, "pct": 80}
    assert rows[0]["alone"] == {"n": 10, "wins": 2, "pct": 20}
    assert rows[0]["cmp"] is not None and rows[0]["maps"][0]["map"] == "de_mirage"


def test_trade_table_counts_avenged_deaths(base):
    import json
    from cs2_coach import duo
    admin = yaml.safe_load(base.read_text(encoding="utf-8"))
    admin["obsidian_vault_path"] = str(base.parent / "vault")
    users.add_user(base, "mate", "geheim1234", "76561190000000002")
    a_dir, m_dir = users.export_dir(admin), users.export_dir(users.merged_config(admin, base, "mate"))
    a_dir.mkdir(parents=True)
    m_dir.mkdir(parents=True)
    me = [{"t": "d", "r": 1, "tk": 1000, "e": "Feind"}, {"t": "d", "r": 2, "tk": 5000, "e": "Feind"},
          {"t": "k", "r": 3, "tk": 9100, "e": "Boss"}]
    mate = [{"t": "k", "r": 1, "tk": 1100, "e": "Feind"},       # raecht Tod 1
            {"t": "k", "r": 2, "tk": 5000 + 64 * 10, "e": "Feind"},  # zu spaet
            {"t": "d", "r": 3, "tk": 9000, "e": "Boss"}]          # von mir geraecht
    (a_dir / "m_coach.json").write_text(json.dumps({"match": {"demo_file": "m.dem"}, "kill_positions": me}), encoding="utf-8")
    (m_dir / "m_coach.json").write_text(json.dumps({"match": {"demo_file": "m.dem"}, "kill_positions": mate}), encoding="utf-8")
    rows = duo.trade_table(admin, base, None)
    assert rows[0]["he_for_you"] == {"n": 2, "k": 1, "pct": 50}
    assert rows[0]["you_for_him"] == {"n": 1, "k": 1, "pct": 100}


def test_team_weekly_sends_once(base, monkeypatch):
    from datetime import date
    from cs2_coach import team_weekly, weekly
    admin = yaml.safe_load(base.read_text(encoding="utf-8"))
    admin["team_webhook"] = "https://discord.invalid/hook"
    assert not team_weekly.run(admin, base, today=date(2026, 11, 30), post=lambda *a, **k: None)  # ohne Mates aus
    users.add_user(base, "mate", "geheim1234", "76561190000000002", "Mate")
    fake = {"matches": 4, "wins": 3, "trainings": 1, "focus": ("Counter-Strafe", "")}
    monkeypatch.setattr(weekly, "summary", lambda cfg, path, today: dict(fake))
    sent = []

    class R:
        status_code = 204
    post = lambda url, json, timeout: sent.append(json["content"]) or R()  # noqa: E731
    assert team_weekly.run(admin, base, today=date(2026, 11, 30), post=post)
    assert "Team-Woche 23.11.–29.11." in sent[0] and "**Mate**: 4 Matches · 3 Siege (75 %)" in sent[0]
    assert "Fokus: Counter-Strafe" in sent[0]
    assert not team_weekly.run(admin, base, today=date(2026, 11, 30), post=post) and len(sent) == 1
