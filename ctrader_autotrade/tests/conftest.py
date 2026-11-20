import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SECRET_VALUES = {"clientID": "cid-SECRET-111", "clientSecret": "csec-SECRET-222",
                 "access_token": "tok-SECRET-333", "refresh_token": "ref-SECRET-444"}


@pytest.fixture
def make_env(tmp_path):
    def _make(**kw):
        vals = {**SECRET_VALUES, "ctidTradeAccountId": "12345", **kw}
        p = tmp_path / ".env"
        p.write_text("# test\n" + "".join(f'{k}="{v}"\n' for k, v in vals.items() if v is not None))
        return p
    return _make


@pytest.fixture
def make_toml(tmp_path):
    def _make(text=""):
        p = tmp_path / "config.toml"
        p.write_text(text)
        return p
    return _make
