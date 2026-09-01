"""Tests fuer die Config-Aufloesung der CLI.

Die Web-App liest CS2COACH_CONFIG, die CLI tat es nicht. Im Container
liegt die Config unter /data/config.yaml, waehrend die CLI stur
/app/config.yaml suchte: die Batch-Analyse brach mit "Config nicht
gefunden" ab, obwohl die Web-Oberflaeche im selben Container einwandfrei
lief.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from cs2_coach.cli import DEFAULT_CONFIG_PATH, _default_config_path, load_config


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("CS2COACH_CONFIG", raising=False)


def test_env_variable_is_honoured(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("player_name: deej0t\n", encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(cfg))

    assert _default_config_path() == cfg
    assert load_config()["player_name"] == "deej0t"


def test_falls_back_to_repo_config(clean_env):
    assert _default_config_path() == DEFAULT_CONFIG_PATH


def test_empty_env_variable_falls_back(monkeypatch):
    monkeypatch.setenv("CS2COACH_CONFIG", "   ")
    assert _default_config_path() == DEFAULT_CONFIG_PATH


def test_explicit_path_beats_env(monkeypatch, tmp_path):
    """Das -c Flag muss Vorrang vor der Umgebungsvariable haben."""
    env_cfg = tmp_path / "env.yaml"
    env_cfg.write_text("player_name: aus_env\n", encoding="utf-8")
    arg_cfg = tmp_path / "arg.yaml"
    arg_cfg.write_text("player_name: aus_argument\n", encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(env_cfg))

    assert load_config(str(arg_cfg))["player_name"] == "aus_argument"


def test_cli_and_webapp_agree_on_the_same_file(monkeypatch, tmp_path):
    """Beide Einstiegspunkte muessen dieselbe Config sehen."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump({"player_name": "deej0t", "steam_id": "123"}), encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(cfg))

    import importlib
    import cs2_coach.web.app as webapp
    importlib.reload(webapp)

    assert Path(webapp.CONFIG_PATH) == _default_config_path()
    assert webapp.load_config() == load_config()
