"""Real input devices (read, sometimes grabbed) and the virtual ones InputForge types with.

Reading: /dev/input/event* through python-evdev (the user is in the `input` group, like the
Key Viewer). Works under Game Mode's keyboard lock and in fullscreen games: it sits below
Hyprland. Devices are only open while the engine needs them (a profile armed, a run, the
recorder...); otherwise nothing reads the keyboard at all.

Grabbing (EVIOCGRAB) is how a key gets blocked or remapped: the device's events then only
reach InputForge, which forwards them through a clone ("InputForge <name>") minus what it
swallows. Safety:
  - a device is only grabbed with no key held (else Hyprland would never see its release)
  - the kernel drops a grab when the process dies, and a watchdog thread ungrabs everything
    if the event loop stalls for WATCHDOG_S, so a hung engine can't eat the keyboard
  - touchpads/touchscreens/tablets (ABS) are never grabbed

Writing: /dev/uinput (uaccess ACL, see data/60-inputforge.rules): one virtual keyboard and
one virtual mouse, created at start (Hyprland needs a moment to pick a new device up).
"""

import os
import threading
import time

import evdev
from evdev import UInput
from evdev import ecodes as e

from inputforge import hypr, keys

PREFIX = "InputForge"
RESCAN_S = 2.0
WATCHDOG_S = 1.5
REL_CODES = [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL, e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES]


class Outputs:
    """The virtual keyboard + mouse. Remembers what it holds so release_all() never leaves a
    key stuck (stop, panic, pause, engine exit)."""

    def __init__(self):
        self.kbd = UInput({e.EV_KEY: keys.KEYBOARD_CODES}, name=f"{PREFIX} keyboard")
        self.mouse = UInput({e.EV_KEY: keys.MOUSE_BUTTONS, e.EV_REL: REL_CODES}, name=f"{PREFIX} mouse")
        self.held = {}  # code -> device it went down on
        self.sink = None  # tests: a list that gets (code, value) instead of real events

    def _dev(self, code):
        return self.mouse if keys.is_mouse(code) else self.kbd

    def key(self, code, value, via=None):
        """value 1 down, 0 up. `via` = a grabbed device's clone that has this code (remaps
        keep the key on the keyboard it came from, so its modifiers apply)."""
        if value and code in self.held:
            return
        if not value and code not in self.held:
            return
        dev = via or self.held.get(code) or self._dev(code)
        if value:
            self.held[code] = dev
        else:
            self.held.pop(code, None)
        if self.sink is not None:
            self.sink.append((keys.name(code), value))
            return
        dev.write(e.EV_KEY, code, value)
        dev.syn()

    def rel(self, dx=0, dy=0):
        if self.sink is not None:
            self.sink.append(("move", dx, dy))
            return
        if dx:
            self.mouse.write(e.EV_REL, e.REL_X, int(dx))
        if dy:
            self.mouse.write(e.EV_REL, e.REL_Y, int(dy))
        self.mouse.syn()

    def scroll(self, dy=0, dx=0):
        """Notches: dy > 0 = up, dx > 0 = right (hi-res events too, 120 per notch)."""
        if self.sink is not None:
            self.sink.append(("scroll", dy, dx))
            return
        if dy:
            self.mouse.write(e.EV_REL, e.REL_WHEEL, int(dy))
            self.mouse.write(e.EV_REL, e.REL_WHEEL_HI_RES, int(dy) * 120)
        if dx:
            self.mouse.write(e.EV_REL, e.REL_HWHEEL, int(dx))
            self.mouse.write(e.EV_REL, e.REL_HWHEEL_HI_RES, int(dx) * 120)
        self.mouse.syn()

    def release(self, codes):
        for c in list(codes):
            if c in self.held:
                self.key(c, 0)

    def release_all(self):
        self.release(list(self.held))

    def close(self):
        self.release_all()
        for d in (self.kbd, self.mouse):
            try:
                d.close()
            except OSError:
                pass


def _kind(dev):
    """'keyboard', 'mouse', 'other' (keys but no letters/buttons) or None (skip)."""
    if dev.name.startswith(PREFIX) or dev.phys == "py-evdev-uinput":
        return None
    caps = dev.capabilities()
    k = set(caps.get(e.EV_KEY, []))
    if not k:
        return None
    if e.EV_ABS in caps:
        return "touch"  # touchpad/touchscreen/tablet: read (clicks) but never grab
    if e.BTN_LEFT in k:
        return "mouse"
    if e.KEY_A in k or e.KEY_ESC in k or e.KEY_F1 in k:
        return "keyboard"
    return "other"


class Device:
    def __init__(self, dev, kind):
        self.dev = dev
        self.kind = kind
        self.path = dev.path
        self.codes = set(dev.capabilities().get(e.EV_KEY, []))
        self.clone = None
        self.grabbed = False
        self.swallowed = set()  # codes whose press was swallowed: swallow their repeat/release too

    def info(self):
        return {"path": self.path, "name": self.dev.name, "kind": self.kind, "grabbed": self.grabbed}


class DeviceManager:
    """on_event(device, event) -> True to swallow (only matters while grabbed)."""

    def __init__(self, loop, outputs, on_event):
        self.loop = loop
        self.outputs = outputs
        self.on_event = on_event
        self.devices = {}  # path -> Device
        self.listening = False
        self.want = set()  # key/button codes that must be swallowable now
        self.grab_all = False  # capture / point picking: every keyboard and mouse
        self.need_mouse = False  # mice send ~1000 events/s while moving: only open them when needed
        self.heartbeat = time.monotonic()
        self._rescan_handle = None
        self._lock = threading.Lock()
        threading.Thread(target=self._watchdog, daemon=True, name="watchdog").start()
        self._beat()

    # ---- lifecycle ---------------------------------------------------------
    def _beat(self):
        self.heartbeat = time.monotonic()
        self.loop.call_later(0.2, self._beat)

    def _watchdog(self):
        while True:
            time.sleep(0.25)
            if time.monotonic() - self.heartbeat > WATCHDOG_S:
                with self._lock:
                    for d in list(self.devices.values()):
                        if d.grabbed:
                            try:
                                d.dev.ungrab()
                            except OSError:
                                pass
                            d.grabbed = False

    def set_need_mouse(self, on):
        if on != self.need_mouse:
            self.need_mouse = on
            if self.listening:
                if self._rescan_handle:
                    self._rescan_handle.cancel()
                self.rescan()

    def listen(self, on):
        if on == self.listening:
            return
        self.listening = on
        if on:
            self.rescan()
        else:
            if self._rescan_handle:
                self._rescan_handle.cancel()
                self._rescan_handle = None
            for path in list(self.devices):
                self._close(path)

    def rescan(self):
        if not self.listening:
            return
        paths = set(evdev.list_devices())
        for path, d in list(self.devices.items()):
            if path not in paths or (d.kind in ("mouse", "touch") and not self.need_mouse):
                self._close(path)
        for path in paths - set(self.devices):
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            kind = _kind(dev)
            if not kind or (kind in ("mouse", "touch") and not self.need_mouse):
                dev.close()
                continue
            d = Device(dev, kind)
            self.devices[path] = d
            self.loop.add_reader(dev.fd, self._readable, d)
        self.update_grabs()
        self._rescan_handle = self.loop.call_later(RESCAN_S, self.rescan)

    def _close(self, path):
        d = self.devices.pop(path, None)
        if not d:
            return
        try:
            self.loop.remove_reader(d.dev.fd)
        except (ValueError, OSError):
            pass
        self._ungrab(d)
        try:
            d.dev.close()
        except OSError:
            pass

    # ---- grabs -------------------------------------------------------------
    def set_want(self, codes, grab_all=None):
        self.want = set(codes)
        if grab_all is not None:
            self.grab_all = grab_all
        self.update_grabs()

    def _should_grab(self, d):
        if d.kind == "touch":
            return False
        if self.grab_all:
            return d.kind in ("keyboard", "mouse")
        return bool(self.want & d.codes)

    def update_grabs(self):
        for d in list(self.devices.values()):
            want = self._should_grab(d)
            if want and not d.grabbed:
                self._grab(d)
            elif not want and d.grabbed:
                self._ungrab(d)

    def _grab(self, d):
        try:
            if d.dev.active_keys():
                # a key is down: Hyprland must see its release first; try again shortly
                self.loop.call_later(0.05, self.update_grabs)
                return
            if d.clone is None:
                d.clone = UInput.from_device(d.dev, name=f"{PREFIX} {d.dev.name}"[:79])
                if d.kind == "keyboard":
                    self.loop.call_later(0.3, hypr.sync_layout, f"inputforge-")
            with self._lock:
                d.dev.grab()
                d.grabbed = True
            d.swallowed.clear()
        except OSError:
            d.grabbed = False

    def _ungrab(self, d):
        if d.clone is not None:
            # release whatever the clone holds, or Hyprland keeps it pressed
            try:
                for c in d.clone.device.active_keys() if d.clone.device else []:
                    d.clone.write(e.EV_KEY, c, 0)
                d.clone.syn()
            except OSError:
                pass
        for c, dev in list(self.outputs.held.items()):
            if dev is d.clone:
                self.outputs.key(c, 0)
        with self._lock:
            if d.grabbed:
                try:
                    d.dev.ungrab()
                except OSError:
                    pass
            d.grabbed = False
        if d.clone is not None:
            try:
                d.clone.close()
            except OSError:
                pass
            d.clone = None

    # ---- events ------------------------------------------------------------
    def _readable(self, d):
        try:
            events = list(d.dev.read())
        except BlockingIOError:
            return
        except OSError:
            self._close(d.path)  # unplugged
            return
        for ev in events:
            swallow = False
            if ev.type == e.EV_KEY:
                # the engine sees every press/release (a remap must release its keys), but a
                # key whose press was swallowed keeps its repeats and release swallowed too
                wants = bool(self.on_event(d, ev))
                if ev.value == 1:
                    swallow = wants and d.grabbed
                    if swallow:
                        d.swallowed.add(ev.code)
                elif ev.code in d.swallowed:
                    swallow = True
                    if ev.value == 0:
                        d.swallowed.discard(ev.code)
            elif ev.type != e.EV_SYN:
                swallow = bool(self.on_event(d, ev)) and d.grabbed
            if d.grabbed and not swallow and d.clone is not None:
                try:
                    d.clone.write(ev.type, ev.code, ev.value)
                except OSError:
                    pass

    def any_held(self):
        held = set()
        for d in self.devices.values():
            try:
                held.update(d.dev.active_keys())
            except OSError:
                pass
        return held

    def info(self):
        return [d.info() for d in self.devices.values()]

    def close(self):
        self.listen(False)
