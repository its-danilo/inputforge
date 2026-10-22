"""Talking to the engine from the CLI, the window and the HUD."""

import json
import os
import socket
import subprocess
import time

from inputforge.paths import SOCKET

from inputforge.paths import UNIT  # noqa: E402


class EngineDown(Exception):
    pass


def call(cmd, timeout=3.0, **args):
    """One request, blocking; raises EngineDown when the engine isn't there."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET)
    except OSError as ex:
        s.close()
        raise EngineDown(str(ex)) from ex
    try:
        s.sendall((json.dumps({"id": 1, "cmd": cmd, **args}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise EngineDown("closed")
            buf += chunk
        reply = json.loads(buf.split(b"\n", 1)[0])
    except (OSError, ValueError) as ex:
        raise EngineDown(str(ex)) from ex
    finally:
        s.close()
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "erro")
    return reply.get("result")


def ensure_engine(wait=4.0):
    """Start the user unit if the engine doesn't answer; True once it does."""
    try:
        call("ping", timeout=1)
        return True
    except EngineDown:
        pass
    subprocess.run(["systemctl", "--user", "start", UNIT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            call("ping", timeout=1)
            return True
        except EngineDown:
            time.sleep(0.15)
    return False


def running():
    return os.path.exists(SOCKET)
