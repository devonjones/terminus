"""Suite-wide fixtures."""

import datetime

import pytest

# Every test sees the same sky. A real `Sky` computes the Sun from
# `sweep._now()`, so a test that fakes the mount's RA/Dec lands that pointing
# somewhere different relative to the Sun at every CI run. terminus-72: the
# dead-channel test fakes RA 12h Dec +20, the September Sun sits within the
# 30 deg cone of it, and in daylight the escape path ran first and raised a
# different error. It failed only on September days (3 of 48 instants
# across a year), so a green run proved nothing. terminus-65 was the same
# failure the other way round (LESSONS.md E-17). A test that wants a
# particular Sun passes `when=` or fakes `Sky.sun`; nothing may depend on
# the wall clock.
#
# A June night in Denver: the Sun is below the horizon, and the full suite
# passes here.
PINNED_UTC = datetime.datetime(2026, 6, 21, 7, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    from astropy.time import Time

    from terminus import sweep

    pinned = Time(PINNED_UTC)
    monkeypatch.setattr(sweep, "_now", lambda: pinned)
