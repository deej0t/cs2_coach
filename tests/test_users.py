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
