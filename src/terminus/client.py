"""Authenticated Seestar S50 client: control (4700) + scenery capture (RTSP 4554).

Handshake for firmware 7.18+: get_verify_str -> sign the challenge with the
interop RSA key (PKCS1v15/SHA1) -> verify_client. The key is extracted once from
the ZWO Android app (see README) and lives outside the repo; terminus never
ships or commits it.

In EQ mode the mount reports reliable RA/Dec but NOT reliable alt/az
(scope_get_horiz_coord is decoupled from true pointing), so terminus points by
converting az/alt -> RA/Dec and issuing scope_goto. Scenery-mode video comes
over RTSP, which ffmpeg reads; the 4700/4800 raw preview is not used.
"""

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time

import numpy as np
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from PIL import Image

CONTROL_PORT = 4700
RTSP_PORT = 4554


class SeestarError(RuntimeError):
    pass


class Seestar:
    def __init__(self, host, pem_path):
        self.host = host
        pem_path = os.path.expanduser(pem_path)
        try:
            with open(pem_path, "rb") as f:
                self.key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
        except FileNotFoundError:
            raise SeestarError(
                f"interop key not found at {pem_path} — see the README for how to extract it"
            ) from None
        self.cmdid = 100
        self.buf = ""
        self.s = None
        self.img = None
        self._reconnecting = False
        self._open()

    # ---- control channel -------------------------------------------------
    def _readline(self, timeout):
        deadline = time.monotonic() + timeout
        while "\r\n" not in self.buf:
            if time.monotonic() > deadline:
                return None
            self.s.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                chunk = self.s.recv(65536)
            except TimeoutError:
                return None
            if not chunk:
                return None
            self.buf += chunk.decode("utf-8", errors="replace")
        line, _, self.buf = self.buf.partition("\r\n")
        return line

    def _open(self):
        """(Re)establish the control connection and re-authenticate."""
        try:
            if self.s is not None:
                self.s.close()
        except OSError:
            pass
        self.buf = ""
        self.s = socket.socket()
        self.s.settimeout(10)
        try:
            self.s.connect((self.host, CONTROL_PORT))
        except OSError as e:
            # A scope fault must carry the scope's error type. socket.timeout is
            # an OSError, so a network failure used to surface as one and get
            # caught by whatever file-handling wrapper happened to be outermost:
            # on 2026-08-05 a mid-run timeout was reported as "could not read or
            # write beside photo_mask.yaml", sending the operator to inspect a
            # file that was perfectly fine, at night, with the mount mid-slew.
            raise SeestarError(
                f"could not reach the scope at {self.host}:{CONTROL_PORT}: {e}. "
                "Check it is powered, awake, and on the same network."
            ) from e

    def reconnect(self):
        """Reopen and re-authenticate after a dropped connection.

        A horizon sweep runs for hours, and the scope will occasionally close
        the socket — an idle moment, a second client touching it, a Wi-Fi blip.
        Without this the whole run dies partway round the circle and the
        remaining sky is simply lost, which is what happened on 2026-08-03.
        """
        if self._reconnecting:
            # authenticate() issues calls of its own; without this guard a
            # socket that fails during re-authentication recurses forever.
            raise SeestarError("connection lost during reconnection")
        self._reconnecting = True
        try:
            self._open()
            if not self.authenticate():
                raise SeestarError("reconnected but authentication failed")
        finally:
            self._reconnecting = False
        return True

    def call(self, method, params=None, timeout=10, _retry=True):
        self.cmdid += 1
        cid = self.cmdid
        msg = {"id": cid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self.s.sendall((json.dumps(msg) + "\r\n").encode())
        except OSError:
            if not _retry:
                raise
            self.reconnect()
            return self.call(method, params, timeout, _retry=False)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._readline(deadline - time.monotonic())
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("id") == cid:
                return r
        return {"timeout": method}

    def authenticate(self):
        resp = self.call("get_verify_str")
        challenge = (
            resp.get("result", {}).get("str", "") if isinstance(resp.get("result"), dict) else ""
        )
        if not challenge:
            return False
        sig = self.key.sign(challenge.encode(), padding.PKCS1v15(), hashes.SHA1())
        vr = self.call("verify_client", {"sign": base64.b64encode(sig).decode(), "data": challenge})
        return isinstance(vr, dict) and vr.get("result", vr.get("code", -1)) == 0

    # ---- mount -----------------------------------------------------------
    def equ_coord(self):
        """(ra_hours, dec_deg) or None."""
        r = self.call("scope_get_equ_coord").get("result")
        return (r["ra"], r["dec"]) if isinstance(r, dict) and "ra" in r else None

    def is_eq_mode(self):
        r = self.call("get_device_state", {"keys": ["mount"]}).get("result", {})
        return bool(r.get("mount", {}).get("equ_mode", False))

    def mount_state(self):
        return self.call("get_device_state", {"keys": ["mount"]}).get("result", {}).get("mount", {})

    def location(self):
        """Scope's stored (lon, lat) or None."""
        r = self.call("get_device_state", {"keys": ["location_lon_lat"]}).get("result", {})
        ll = r.get("location_lon_lat")
        return (ll[0], ll[1]) if ll else None

    def goto(self, ra_hours, dec_deg):
        return self.call("scope_goto", [ra_hours, dec_deg])

    # ---- imaging ---------------------------------------------------------
    def start_view(self, mode="scenery"):
        return self.call("iscope_start_view", {"mode": mode})

    def lock_exposure(self, exp_ms=None, gain=None):
        """Fix exposure and gain so frame brightness means something.

        With auto-exposure the camera normalises every frame toward mid-grey,
        which cancels the very sky-versus-terrain brightness difference the sweep
        measures — measured profiles then hover at one level regardless of where
        the scope points. Locking makes brightness comparable between pointings.
        Returns the settings the scope reports afterwards, and raises if the lock
        did not take. Verifying matters: the scope has been observed to accept
        set_setting and still report manual_exp False with the auto sentinels
        (-999000 / -9990). Silently continuing then measures brightness under
        auto-exposure, which is the one condition this call exists to prevent —
        every column reads the same and the whole sweep is quietly worthless.
        """
        params = {"manual_exp": True}
        if exp_ms is not None:
            params["isp_exp_ms"] = exp_ms
        if gain is not None:
            params["isp_gain"] = gain
        self.call("set_setting", params)
        s = self.call("get_setting").get("result", {})
        got = {k: s.get(k) for k in ("manual_exp", "isp_exp_ms", "isp_gain")}
        if not got.get("manual_exp"):
            raise SeestarError(
                f"exposure lock failed: scope still reports {got}. "
                "Brightness would not be comparable between pointings; refusing to sweep."
            )
        # The scope may clamp the requested values (scenery mode has its own
        # limits). That is acceptable — consistency between frames is what the
        # sweep needs — but say so rather than let it pass unremarked.
        for key, want in (("isp_exp_ms", exp_ms), ("isp_gain", gain)):
            if want is not None and got.get(key) is not None:
                if abs(float(got[key]) - float(want)) > 0.05 * max(abs(float(want)), 1.0):
                    got.setdefault("clamped", []).append(f"{key}: asked {want}, got {got[key]}")
        return got

    def auto_exposure(self):
        """Hand exposure back to the camera."""
        self.call("set_setting", {"manual_exp": False})

    def stop_view(self):
        return self.call("iscope_stop_view", {"stage": "Stack"})

    # ---- imaging channel (4800): star-mode raw frames ---------------------
    IMG_HEADER = ">HHHIHHBBHH"  # first 20 of 80 bytes: size@3, id@7, width@8, height@9
    IMAGING_PORT = 4800

    def _open_imaging(self):
        self.close_imaging()
        self.img = socket.socket()
        self.img.settimeout(30)
        self.img.connect((self.host, self.IMAGING_PORT))
        self.img.sendall(b'{"id": 21, "method": "begin_streaming"}\r\n')

    def close_imaging(self):
        try:
            if self.img:
                self.img.close()
        except OSError:
            pass
        self.img = None

    def _img_exact(self, n):
        b = b""
        while len(b) < n:
            c = self.img.recv(n - len(b))
            if not c:
                raise ConnectionError("imaging socket closed mid-frame")
            b += c
        return b

    def _raw16_frame(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = struct.unpack(self.IMG_HEADER, self._img_exact(80)[:20])
            size, mid, w, h = v[3], v[7], v[8], v[9]
            data = self._img_exact(size) if size else b""
            if size == w * h * 2 and mid == 21:
                return np.frombuffer(data, dtype="<u2").reshape(h, w)
        raise TimeoutError(f"no raw16 frame in {timeout}s")

    def capture_raw16_median(self, exposure_s=2.0):
        """Median of `capture_raw16`, which is the night measurement itself.

        MEDIAN, not mean: hot pixels and streetlights are bright outliers
        sitting INSIDE terrain, and the mean follows them (M-12). Exposure,
        draining and the reconnect all belong to `capture_raw16` below.
        """
        return float(np.median(self.capture_raw16(exposure_s)))

    def capture_raw16(self, exposure_s=2.0):
        """One raw 16-bit frame EXPOSED at the current pointing.

        The night capture path. The scenery RTSP stream is blind after dark —
        measured 2026-08-06: its ISP pins exposure at ~30 ms and gain at 112.5
        whatever is requested, and sky and terrain then differ by 0.02 counts in
        255. Star mode exposes for seconds, but serves no RTSP; its frames
        arrive raw on the imaging channel instead. `start_view("star")` must be
        active.

        Frames already in flight when the mount arrives were exposed somewhere
        else, so the stream is drained through one full exposure before the
        counted frame — skipping that reads the previous pointing's sky at the
        new pointing's label.

        One silent reconnect: the scope has been seen to reset this socket after
        a burst of failed gotos, and a dead socket must not poison every later
        column.

        Returns the frame rather than only its median so the night scan can
        keep the pixels it measured: a median cannot say WHAT it was a median
        of, and cloud, canopy and a lit wall all make honest brightness steps.
        The day path has saved its frames from the start; this one kept a
        single count per sample and discarded the evidence.
        """
        if self.img is None:
            self._open_imaging()
        try:
            deadline = time.time() + exposure_s + 0.5
            while time.time() < deadline:
                self._raw16_frame()
            return self._raw16_frame()
        except (ConnectionError, OSError, TimeoutError):
            self._open_imaging()
            deadline = time.time() + exposure_s + 0.5
            while time.time() < deadline:
                self._raw16_frame()
            return self._raw16_frame()

    def capture_rgb(self, warmup=1.0, retries=2):
        """One RGB frame (float32 HxWx3) from the scenery RTSP stream via ffmpeg.

        start_view('scenery') must be active. `warmup` skips stale buffered frames.

        A frame IDENTICAL to the previous one is rejected and re-taken. That is
        the detectable form of the failure worth guarding against: the stream has
        been seen to freeze after hours of use, serving one stale frame however
        the mount moved, which fabricated ten blocked azimuths out of open sky
        before anyone noticed.

        Note what this deliberately does NOT do. An earlier version rejected
        frames that were merely dark, which is wrong twice over: genuinely unlit
        terrain reads about 0.09 counts and would be retried on every sample,
        while the frozen frames that motivated the check read 0.8 — brighter than
        real terrain. Darkness cannot separate them; repetition can.
        """
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            path = tf.name
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-i",
                    f"rtsp://{self.host}:{RTSP_PORT}/stream",
                    "-ss",
                    str(warmup),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    path,
                ],
                check=True,
                timeout=30,
            )
            rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
            sig = (float(rgb.mean()), float(rgb.std()), float(rgb[::37, ::37].sum()))
            if retries > 0 and sig == getattr(self, "_last_frame_sig", None):
                time.sleep(0.6)
                return self.capture_rgb(warmup=warmup + 0.4, retries=retries - 1)
            self._last_frame_sig = sig
            return rgb
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise SeestarError(f"RTSP capture failed (is scenery view running?): {e}") from e
        finally:
            if os.path.exists(path):
                os.remove(path)

    def close(self):
        self.close_imaging()
        try:
            self.s.close()
        except OSError:
            pass
