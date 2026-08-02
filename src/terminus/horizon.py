"""The planning-facing side of terminus: a measured horizon you can query.

A planner (SSC, N.I.N.A. sequencing, a custom scheduler) doesn't want the sweep
machinery — it wants to ask "is this target clear of my real horizon right now?"
Horizon answers that from a measured mask, with no telescope or network needed.
"""

import bisect


class Horizon:
    """A local horizon: interpolated obstruction altitude vs true azimuth."""

    def __init__(self, points, meta=None):
        """points: iterable of (azimuth_deg, altitude_deg). Azimuth 0 = true
        north, increasing toward east. Wrap endpoints are added automatically."""
        pts = sorted((float(a) % 360.0, float(alt)) for a, alt in points)
        if len(pts) < 2:
            raise ValueError("a horizon needs at least two points")
        # guarantee coverage across the 0/360 wrap for clean interpolation
        if pts[0][0] != 0.0:
            pts.insert(0, (0.0, pts[-1][1]))  # just below az 0 wraps from the top
        if pts[-1][0] != 360.0:
            pts.append((360.0, pts[0][1]))
        self._az = [p[0] for p in pts]
        self._alt = [p[1] for p in pts]
        self.meta = meta or {}

    @classmethod
    def from_mask(cls, path):
        from .export import load_mask

        meta, rows = load_mask(path)
        return cls([(az, alt) for az, alt, _ in rows], meta)

    def altitude_at(self, az):
        """Interpolated horizon altitude (deg) at azimuth `az`."""
        az = float(az) % 360.0
        i = bisect.bisect_right(self._az, az)
        if i == 0:
            return self._alt[0]
        if i >= len(self._az):
            return self._alt[-1]
        a0, a1 = self._az[i - 1], self._az[i]
        h0, h1 = self._alt[i - 1], self._alt[i]
        if a1 == a0:
            return h1
        return h0 + (h1 - h0) * (az - a0) / (a1 - a0)

    def is_above(self, az, alt):
        """True if a point at (az, alt) clears the local horizon."""
        return float(alt) > self.altitude_at(az)

    def is_visible(self, ra_hours, dec_deg, sky, when=None):
        """True if the RA/Dec target currently clears the local horizon, given a
        terminus.Sky (site) and optional astropy Time `when`."""
        az, alt = sky.radec_to_altaz(ra_hours, dec_deg, when)
        return self.is_above(az, alt)

    def clearance(self, az, alt):
        """Degrees a point sits above (positive) or below (negative) the horizon."""
        return float(alt) - self.altitude_at(az)
