import logging
import pickle
from pathlib import Path

import pytest

from autotrade import config
from autotrade.config import ConfigError, Settings, load
from tests.conftest import SECRET_VALUES

REAL_TOML = Path(config.ROOT) / "config.toml"


def test_account_alias_trader_spelling(make_env):
    env = make_env(ctidTradeAccountId=None, ctidTraderAccountId="777")
    assert load(env, REAL_TOML).secrets.account_id == 777


def test_account_both_same_ok(make_env):
    env = make_env(ctidTraderAccountId="12345")
    assert load(env, REAL_TOML).secrets.account_id == 12345


def test_account_conflict(make_env):
    env = make_env(ctidTraderAccountId="999")
    with pytest.raises(ConfigError, match="differ"):
        load(env, REAL_TOML)


@pytest.mark.parametrize("name", ["clientID", "clientSecret", "access_token"])
def test_missing_required(make_env, name):
    with pytest.raises(ConfigError, match=name):
        load(make_env(**{name: None}), REAL_TOML)


def test_missing_account(make_env):
    with pytest.raises(ConfigError, match="ctidTradeAccountId"):
        load(make_env(ctidTradeAccountId=None), REAL_TOML)


def test_refresh_optional(make_env):
    assert load(make_env(refresh_token=None), REAL_TOML).secrets.refresh_token is None


def test_quotes_stripped(make_env):
    s = load(make_env(), REAL_TOML).secrets
    assert s.client_id == SECRET_VALUES["clientID"]


def test_secrets_never_in_repr_str_log(make_env, caplog):
    s = load(make_env(), REAL_TOML)
    caplog.set_level(logging.DEBUG)
    logging.getLogger("x").info("%s %r %s", s.secrets, s.secrets, s)
    text = repr(s) + str(s) + repr(s.secrets) + str(s.secrets) + caplog.text + f"{s.secrets}"
    for v in SECRET_VALUES.values():
        assert v not in text
    assert "12345" not in text
    with pytest.raises(TypeError):
        pickle.dumps(s.secrets)


def test_toml_defaults(make_env):
    s = load(make_env(), REAL_TOML)
    assert s.symbols == ["XAUUSD", "BTCUSD"]
    assert "GOLD" in s.aliases["XAUUSD"] and "BITCOIN" in s.aliases["BTCUSD"]
    assert (s.host, s.port) == ("demo.ctraderapi.com", 5035)
    assert (s.entry_z, s.vol_shrink_z, s.confirm_range, s.exec) == (1.0, 2.0, "off", "taker")
    assert (s.reject_breaker_count, s.reject_breaker_window_min) == (3, 10)
    assert s.label == "axq-poc4h-v1" and s.warmup_hours == 9 and s.daily_loss_bps == 150
    d = Settings()
    for f in ("entry_z", "stop_k", "max_positions", "stale_s", "close_on_shutdown", "grace_s"):
        assert getattr(s, f) == getattr(d, f)


def test_no_toml_uses_defaults(make_env, tmp_path):
    s = load(make_env(), tmp_path / "missing.toml")
    assert s.entry_z == 1.0 and s.host == "demo.ctraderapi.com"


def test_live_host_refused(make_env, make_toml):
    toml = make_toml('host = "live.ctraderapi.com"\n')
    with pytest.raises(ConfigError, match="refused"):
        load(make_env(), toml)
    with pytest.raises(ConfigError, match="refused"):
        load(make_env(), REAL_TOML, overrides={"host": "live.ctraderapi.com"})
    assert load(make_env(), toml, allow_live_host=True).host == "live.ctraderapi.com"


def test_overrides(make_env, make_toml):
    toml = make_toml("[strategy]\nentry_z = 1.5\n")
    assert load(make_env(), toml).entry_z == 1.5
    s = load(make_env(), toml, overrides={"entry_z": 0.8, "exec": "maker"})
    assert (s.entry_z, s.exec) == (0.8, "maker")


def test_unknown_setting(make_env, make_toml):
    with pytest.raises(ConfigError, match="unknown"):
        load(make_env(), make_toml("bogus = 1\n"))
