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
        self.s = socket.socket()
        self.s.settimeout(10)
        self.s.connect((host, CONTROL_PORT))

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

    def call(self, method, params=None, timeout=10):
        self.cmdid += 1
        cid = self.cmdid
        msg = {"id": cid, "method": method}
        if params is not None:
            msg["params"] = params
        self.s.sendall((json.dumps(msg) + "\r\n").encode())
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
        Returns the settings the scope reports afterwards.
        """
        params = {"manual_exp": True}
        if exp_ms is not None:
            params["isp_exp_ms"] = exp_ms
        if gain is not None:
            params["isp_gain"] = gain
        self.call("set_setting", params)
        s = self.call("get_setting").get("result", {})
        return {k: s.get(k) for k in ("manual_exp", "isp_exp_ms", "isp_gain")}

    def auto_exposure(self):
        """Hand exposure back to the camera."""
        self.call("set_setting", {"manual_exp": False})

    def stop_view(self):
        return self.call("iscope_stop_view", {"stage": "Stack"})

    def capture_rgb(self, warmup=1.0):
        """One RGB frame (float32 HxWx3) from the scenery RTSP stream via ffmpeg.
        start_view('scenery') must be active. `warmup` skips stale buffered frames."""
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
            return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise SeestarError(f"RTSP capture failed (is scenery view running?): {e}") from e
        finally:
            if os.path.exists(path):
                os.remove(path)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass
