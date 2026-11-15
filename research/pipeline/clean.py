"""Cleaning: UTC timestamps, de-duplication, and a regular 5-minute grid per instrument."""
import pandas as pd

from . import INSTRUMENT

SLOT = pd.Timedelta(minutes=5)


def clean_metrics(df):
    """Return bars on a gap-free 5-minute grid per instrument.

    Missing slots and has_data=0 slots are kept as rows with NaN market fields so
    that shifts in features.py are time-correct (shift(1) == previous 5 minutes).
    """
    if df.empty:
        return df.copy()
    df = df.copy()
    df["slot_end"] = pd.to_datetime(df["slot_end"], utc=True)
    df["slot_start"] = pd.to_datetime(df["slot_start"], utc=True)
    df = df.sort_values("computed_at" if "computed_at" in df else "slot_end")
    df = df.drop_duplicates(INSTRUMENT + ["slot_end"], keep="last")

    static = [c for c in ("broker", "symbol_name", "standardized_name", "asset_class") if c in df]
    market = [c for c in df.columns if c not in INSTRUMENT + static + ["slot_end", "slot_start", "has_data", "computed_at"]]
    df["has_data"] = df["has_data"].fillna(0).astype(bool) & df["mid_close"].notna()
    df.loc[~df["has_data"], market] = float("nan")

    out = []
    for key, g in df.groupby(INSTRUMENT, sort=True):
        grid = pd.date_range(g["slot_end"].min(), g["slot_end"].max(), freq=SLOT)
        g = g.set_index("slot_end").reindex(grid)
        g.index.name = "slot_end"
        g[INSTRUMENT] = key
        g[static] = g[static].ffill().bfill()
        g["has_data"] = g["has_data"].fillna(False).astype(bool)
        g["slot_start"] = g.index - SLOT
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)
