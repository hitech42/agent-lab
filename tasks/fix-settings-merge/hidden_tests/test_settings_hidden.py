import copy

import pytest
from settings import load_settings, merge_settings, parse_env


def test_nested_merge_keeps_siblings_at_every_depth():
    base = {"a": {"b": {"c": 1, "d": 2}, "e": 3}, "f": 4}
    override = {"a": {"b": {"c": 10}}}
    assert merge_settings(base, override) == {"a": {"b": {"c": 10, "d": 2}, "e": 3}, "f": 4}


def test_a_dict_replaces_a_scalar_and_a_scalar_replaces_a_dict():
    assert merge_settings({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert merge_settings({"a": {"b": 2}}, {"a": 1}) == {"a": 1}


def test_lists_are_replaced_not_concatenated():
    assert merge_settings({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


def test_none_removes_keys_at_any_depth_and_is_ignored_when_absent():
    assert merge_settings({"a": 1, "b": 2}, {"a": None}) == {"b": 2}
    assert merge_settings({"a": {"b": 1, "c": 2}}, {"a": {"b": None}}) == {"a": {"c": 2}}
    assert merge_settings({"a": 1}, {"zzz": None}) == {"a": 1}
    assert merge_settings({"a": 1}, {"new": {"x": None, "y": 2}}) == {"a": 1, "new": {"y": 2}}


def test_neither_input_is_modified():
    base = {"a": {"b": [1, 2]}, "c": {"d": 1}}
    override = {"a": {"b": [3]}, "c": {"e": {"f": [4]}}, "g": None}
    before = copy.deepcopy((base, override))
    merge_settings(base, override)
    assert (base, override) == before


def test_the_result_shares_nothing_with_the_inputs():
    base = {"keep": {"x": [1]}, "mixed": {"y": 1}}
    override = {"new": {"z": [2]}, "mixed": {"w": {"v": 1}}}
    result = merge_settings(base, override)
    result["keep"]["x"].append(99)
    result["keep"]["added"] = True
    result["new"]["z"].append(99)
    result["mixed"]["w"]["v"] = 99
    assert base == {"keep": {"x": [1]}, "mixed": {"y": 1}}
    assert override == {"new": {"z": [2]}, "mixed": {"w": {"v": 1}}}


def test_env_names_nest_on_double_underscores_and_are_lower_cased():
    env = {"APP_DB__HOST": "h", "APP_DB__PORT": "5432", "APP_A__B__C": "x", "APP_LOG_LEVEL": "info"}
    assert parse_env(env) == {
        "db": {"host": "h", "port": 5432},
        "a": {"b": {"c": "x"}},
        "log_level": "info",
    }


def test_env_prefix_filtering():
    env = {"APP_X": "1", "OTHER_Y": "2", "APP_": "3", "MYAPP_Z": "4"}
    assert parse_env(env) == {"x": 1}
    assert parse_env({"MY_Z": "4", "APP_X": "1", "MY_": "5"}, prefix="MY_") == {"z": 4}


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("true", True),
        ("TRUE", True),
        ("False", False),
        ("null", None),
        ("NULL", None),
        ("0", 0),
        ("42", 42),
        ("-5", -5),
        ("-0", 0),
        ("1.5", "1.5"),
        ("+5", "+5"),
        ("5x", "5x"),
        ("--5", "--5"),
        ("", ""),
        ("yes", "yes"),
    ],
)
def test_env_values_are_converted(raw, value):
    result = parse_env({"APP_V": raw})
    assert result["v"] == value
    assert type(result["v"]) is type(value)


def test_load_settings_layers_in_order_with_the_environment_last():
    defaults = {"port": 1, "db": {"host": "a", "pool": 5}, "debug": True}
    layers = [{"port": 2, "db": {"host": "b"}}, {"port": 3}]
    environ = {"APP_DB__POOL": "9", "APP_DEBUG": "null", "OTHER": "x"}
    assert load_settings(defaults, layers, environ) == {"port": 3, "db": {"host": "b", "pool": 9}}


def test_load_settings_does_not_modify_the_defaults_or_share_them():
    defaults = {"db": {"host": "a"}}
    result = load_settings(defaults, [], {})
    result["db"]["host"] = "changed"
    assert defaults == {"db": {"host": "a"}}


def test_load_settings_uses_a_custom_prefix():
    assert load_settings({}, [], {"X_PORT": "-1", "APP_PORT": "2"}, prefix="X_") == {"port": -1}
