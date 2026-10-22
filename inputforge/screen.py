"""Screen observer: what the macros' wait_screen / if_screen steps and screen triggers see.

A region's signature is a GRID x GRID grid of average colors (config.GRID). Comparing two
signatures counts the cells whose color moved more than CELL_DIFF on any channel: a skill
slot's cooldown fill, a "!" popping up or a button appearing flips many cells, while
compression noise or a slight glow flips none.

Captures come from grim (wlr-screencopy): one capture of the bounding box of every region
in use, RATE times per second, and only while something subscribes (a running macro that
uses regions, an armed profile with screen triggers). Nothing runs otherwise.
"""

import asyncio
import sys
import time

from inputforge.config import GRID

RATE = 15  # captures per second while something watches
CELL_DIFF = 24  # 0-255: a cell "differs" when a channel moved more than this
SAMPLES = 6  # points per cell side
MAX_BBOX = 2_000_000  # px: regions spread over a bigger area are captured one by one


def rect(region):
    return (region["x"], region["y"], max(1, region["w"]), max(1, region["h"]))


def differ_pct(a, b):
    """% of cells that changed between two signatures."""
    if not a or not b or len(a) != len(b):
        return 100.0
    n = sum(1 for p, q in zip(a, b) if max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2])) > CELL_DIFF)
    return 100.0 * n / len(a)


def check(sig, region, cond, base=None):
    """Is `cond` true for this signature? match/differ use the region's reference;
    change compares with `base` (the signature when the wait began)."""
    if cond == "change":
        return base is not None and differ_pct(sig, base) > region["tolerance"]
    if not region.get("ref"):
        return False
    same = differ_pct(sig, region["ref"]) <= region["tolerance"]
    return same if cond == "match" else not same


def _signature(data, width, px, py, pw, ph):
    out = []
    for gy in range(GRID):
        y0 = py + ph * gy // GRID
        y1 = max(y0 + 1, py + ph * (gy + 1) // GRID)
        ys = range(y0, y1, max(1, (y1 - y0) // SAMPLES))
        for gx in range(GRID):
            x0 = px + pw * gx // GRID
            x1 = max(x0 + 1, px + pw * (gx + 1) // GRID)
            xs = range(x0, x1, max(1, (x1 - x0) // SAMPLES))
            r = g = b = n = 0
            for y in ys:
                row = y * width * 3
                for x in xs:
                    i = row + x * 3
                    r += data[i]
                    g += data[i + 1]
                    b += data[i + 2]
                    n += 1
            out.append([r // n, g // n, b // n])
    return out


async def _grim(x, y, w, h):
    proc = await asyncio.create_subprocess_exec(
        "grim", "-t", "ppm", "-g", f"{x},{y} {w}x{h}", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    raw, _ = await proc.communicate()
    parts = raw.split(b"\n", 3)  # P6 / width height / 255 / pixels
    if len(parts) < 4 or parts[0] != b"P6":
        raise OSError("grim: no image")
    width, height = map(int, parts[1].split())
    return parts[3], width, height


async def capture(rects):
    """{rect: signature} for these rects (layout coordinates), in as few grim calls as possible."""
    out = {}
    if not rects:
        return out
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    groups = [((x0, y0, x1 - x0, y1 - y0), rects)]
    if (x1 - x0) * (y1 - y0) > MAX_BBOX:
        groups = [(r, [r]) for r in rects]
    for (bx, by, bw, bh), members in groups:
        data, width, height = await _grim(bx, by, bw, bh)
        sx, sy = width / bw, height / bh  # output scale (HiDPI)
        for r in members:
            px, py = int((r[0] - bx) * sx), int((r[1] - by) * sy)
            pw = max(1, min(int(r[2] * sx), width - px))
            ph = max(1, min(int(r[3] * sy), height - py))
            out[r] = _signature(data, width, px, py, pw, ph)
    return out


class Watcher:
    def __init__(self, loop):
        self.loop = loop
        self.subs = {}  # token -> set of rects
        self.latest = {}  # rect -> (monotonic time, signature)
        self.frame = asyncio.Event()
        self.task = None
        self.errors = 0
        self._token = 0

    def subscribe(self, regions):
        self._token += 1
        self.subs[self._token] = {rect(r) for r in regions if r and r["w"] and r["h"]}
        if self.subs[self._token] and not self.task:
            self.task = self.loop.create_task(self._run())
        return self._token

    def unsubscribe(self, token):
        self.subs.pop(token, None)

    def active(self):
        return any(self.subs.values())

    async def _run(self):
        try:
            while self.active():
                t0 = self.loop.time()
                rects = set().union(*self.subs.values())
                try:
                    now = time.monotonic()
                    for r, sig in (await capture(list(rects))).items():
                        self.latest[r] = (now, sig)
                    self.errors = 0
                except (OSError, ValueError) as ex:
                    self.errors += 1
                    if self.errors in (1, 50):
                        print("screen capture failed:", ex, file=sys.stderr, flush=True)
                    await asyncio.sleep(0.5)
                done, self.frame = self.frame, asyncio.Event()
                done.set()
                await asyncio.sleep(max(0.0, 1 / RATE - (self.loop.time() - t0)))
        finally:
            self.task = None
            done, self.frame = self.frame, asyncio.Event()
            done.set()

    async def next_frame(self):
        await self.frame.wait()

    async def sample(self, region, max_age=1.5 / RATE):
        """A signature of the region no older than max_age (waits for the next capture)."""
        r = rect(region)
        token = self.subscribe([region])
        try:
            for _ in range(RATE * 10):
                got = self.latest.get(r)
                if got and time.monotonic() - got[0] <= max_age:
                    return got[1]
                await self.next_frame()
            raise OSError("screen capture stalled")
        finally:
            self.unsubscribe(token)

    async def capture_now(self, region):
        r = rect(region)
        return (await capture([r]))[r]
