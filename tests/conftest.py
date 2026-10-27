import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from generate_data import build  # noqa: E402

from anomaly_agent import loader  # noqa: E402

COLS = ["revenue", "orders", "traffic", "conversion_rate", "cost", "refunds"]


@pytest.fixture(scope="session")
def sales():
    raw, truth = build(7)
    df, dq = loader.prepare(raw)
    return df, truth


@pytest.fixture()
def flat_noise():
    """60 days of mild noise around 100 with a known spike on day 45."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-01-01", periods=60, freq="D", name="date")
    s = pd.Series(100 + rng.normal(0, 2, 60), index=idx, name="x")
    s.iloc[45] = 160
    return pd.DataFrame({"x": s})
