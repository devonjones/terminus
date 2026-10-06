"""Suite-wide fixtures."""

import datetime

import pytest

# Every test sees the same sky: no test may depend on when CI runs
# (LESSONS.md E-17). A test that wants a particular Sun passes `when=` or fakes
# `Sky.sun`. A June night in Denver, so the Sun is below the horizon.
PINNED_UTC = datetime.datetime(2026, 6, 21, 7, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    from astropy.time import Time

    from terminus import sweep

    pinned = Time(PINNED_UTC)
    monkeypatch.setattr(sweep, "_now", lambda: pinned)
