import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import fetch_story as fs


def test_series_active_subs_returns_at_most_k():
    stable = fs.SERIES_SUBS + ["antiwork", "SomeOtherSub"]
    picks = fs.series_active_subs(stable, k=4)
    assert len(picks) <= 4
    assert len(picks) == len(set(picks))  # no duplicates


def test_series_active_subs_falls_back_to_plain_sampling_if_no_series_sub_available():
    random.seed(1)
    stable = ["totally_unrelated_sub_1", "totally_unrelated_sub_2"]
    picks = fs.series_active_subs(stable, k=2)
    assert set(picks) <= set(stable)


def test_series_active_subs_favors_the_series_cluster():
    random.seed(1)
    stable = fs.SERIES_SUBS[:3] + ["totally_unrelated_sub"]
    # off_brand_p=0.0 means it should never reach for the non-series sub
    picks = fs.series_active_subs(stable, k=3, off_brand_p=0.0)
    assert set(picks) <= set(fs.SERIES_SUBS)
