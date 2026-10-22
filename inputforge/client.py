"""The engine connection for the GTK window: one subscribed socket in the GLib main loop.

  client.request("set_config", {"config": cfg}, on_reply)   # on_reply(ok, result) later
  client.on_event = fn                                     # fn({"event": "state", ...})
  client.on_up = fn                                        # fn(True/False): engine there or not
It reconnects every 2 s while the engine is down.
"""

import json
import socket

from gi.repository import GLib

from inputforge.paths import SOCKET

SUB = "sub"


class Client:
    def __init__(self):
        self.on_event = lambda msg: None
        self.on_up = lambda up: None
        self.sock = None
        self.buf = b""
        self.watch = None
        self.up = False
        self.seq = 0
        self.pending = {}  # id -> callback(ok, result)
        self.connect()

    def connect(self):
        if self.sock:
            return True
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(SOCKET)
        except OSError:
            s.close()
            self._set_up(False)
            return False
        self.sock = s
        self.buf = b""
        s.sendall(json.dumps({"id": SUB, "cmd": "subscribe"}).encode() + b"\n")
        s.setblocking(False)
        self.watch = GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT,
                                       GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                                       self._readable)
        self._set_up(True)
        return True

    def _set_up(self, up):
        if up != self.up:
            self.up = up
            self.on_up(up)

    def _drop(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.sock:
            self.sock.close()
            self.sock = None
        for cb in self.pending.values():
            if cb:
                cb(False, "motor parado")
        self.pending.clear()
        self._set_up(False)
        GLib.timeout_add(2000, lambda: not self.connect())

    def _readable(self, fd, cond):
        try:
            chunk = self.sock.recv(1 << 20)
        except BlockingIOError:
            return True
        except OSError:
            chunk = b""
        if not chunk:
            self.watch = None
            self._drop()
            return False
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "event" in msg:
                self.on_event(msg)
            elif msg.get("id") in self.pending:
                cb = self.pending.pop(msg["id"])
                if cb:
                    cb(bool(msg.get("ok")), msg.get("result") if msg.get("ok") else msg.get("error"))
        return True

    def request(self, cmd, args=None, callback=None):
        if not self.sock and not self.connect():
            if callback:
                GLib.idle_add(lambda: callback(False, "motor parado") and False)
            return
        self.seq += 1
        self.pending[self.seq] = callback
        try:
            self.sock.setblocking(True)
            self.sock.sendall(json.dumps({"id": self.seq, "cmd": cmd, **(args or {})}).encode() + b"\n")
            self.sock.setblocking(False)
        except OSError:
            self._drop()
