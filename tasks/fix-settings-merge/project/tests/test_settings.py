import json
from pathlib import Path

from settings import load_settings, merge_settings, parse_env

DEFAULTS = json.loads((Path(__file__).parent / "data" / "defaults.json").read_text())


def test_nested_dicts_merge_and_other_values_replace():
    base = {"db": {"host": "a", "port": 1}, "tags": [1, 2], "debug": False}
    override = {"db": {"port": 2}, "tags": [3], "debug": True}
    assert merge_settings(base, override) == {
        "db": {"host": "a", "port": 2},
        "tags": [3],
        "debug": True,
    }


def test_a_local_override_can_remove_a_defaulted_setting():
    merged = merge_settings(DEFAULTS, {"service": {"retries": None}})
    service = {key: value for key, value in DEFAULTS["service"].items() if key != "retries"}
    assert merged == {**DEFAULTS, "service": service}


def test_none_removes_a_key():
    assert merge_settings({"a": 1, "b": 2}, {"a": None}) == {"b": 2}


def test_env_names_nest_on_double_underscores():
    assert parse_env({"APP_DB__HOST": "x", "APP_LOG_LEVEL": "debug"}) == {
        "db": {"host": "x"},
        "log_level": "debug",
    }


def test_env_values_are_converted():
    env = {"APP_PORT": "8080", "APP_DEBUG": "true", "APP_OFF": "FALSE", "APP_NAME": "web"}
    assert parse_env(env) == {"port": 8080, "debug": True, "off": False, "name": "web"}


def test_negative_numbers_are_numbers():
    assert parse_env({"APP_OFFSET": "-5"}) == {"offset": -5}


def test_the_environment_wins_over_files():
    settings = load_settings({"port": 1}, [{"port": 2}], {"APP_PORT": "3"})
    assert settings == {"port": 3}
