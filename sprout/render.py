"""Draws the tree.

Every random choice is seeded from the URI it belongs to, so the same tree
always renders the same way and a new like adds a leaf instead of reshuffling
the crown.

The crown is laid out as a fan above the trunk: each branch owns a slice of
it and hands sub-slices to its children in proportion to how much grows on
them, so a busy branch spreads wide and the crown stays round however lopsided
the spread was. Layout is in abstract units with the trunk base at (0, 0) and
y up; only at the end is it scaled onto the canvas.
"""

from __future__ import annotations

import bisect
import colorsys
import hashlib
import io
import math
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .tree import Branch

SS = 2  # draw at twice the size and scale down: Pillow does not antialias shapes
UPLOAD_LIMIT = 976_000  # Bluesky rejects image blobs over 1 MB

PAPER_TOP = (246, 240, 228)
PAPER_BOTTOM = (233, 222, 201)
HILL = (219, 205, 177)
SHADOW = (70, 52, 38, 34)
BARK = (72, 56, 45)
BARK_YOUNG = (128, 104, 82)
PETALS = [(240, 160, 178), (245, 206, 96), (252, 248, 243), (230, 138, 120)]
POLLEN = (204, 138, 52)

FAN = 1.3  # half-width of the crown, in radians from vertical
TRUNK_HEIGHT = 0.62
LEAF_WEIGHT = 0.25  # how much a leaf thickens the branch it hangs on
TAPER = 0.45  # a branch's tip is this much thinner than its base


def _rng(key: str) -> random.Random:
    digest = hashlib.blake2b(key.encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


@dataclass
class _Seg:
    branch: Branch
    p0: tuple[float, float]
    c: tuple[float, float]  # quadratic Bézier control point
    p1: tuple[float, float]
    width: float

    def at(self, t: float) -> tuple[float, float]:
        u = 1 - t
        return (
            u * u * self.p0[0] + 2 * u * t * self.c[0] + t * t * self.p1[0],
            u * u * self.p0[1] + 2 * u * t * self.c[1] + t * t * self.p1[1],
        )

    def tangent(self, t: float) -> tuple[float, float]:
        u = 1 - t
        return (
            2 * u * (self.c[0] - self.p0[0]) + 2 * t * (self.p1[0] - self.c[0]),
            2 * u * (self.c[1] - self.p0[1]) + 2 * t * (self.p1[1] - self.c[1]),
        )


def _curve(
    b: Branch,
    p0: tuple[float, float],
    p1: tuple[float, float],
    bend: float,
    width: float,
) -> _Seg:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    c = ((p0[0] + p1[0]) / 2 + dy * bend, (p0[1] + p1[1]) / 2 - dx * bend)
    return _Seg(b, p0, c, p1, width)


def _weights(trunk: Branch) -> dict[Branch, float]:
    w: dict[Branch, float] = {}
    for b in reversed(list(trunk.walk())):
        w[b] = (
            1
            + LEAF_WEIGHT * (len(b.leaves) + len(b.flowers))
            + sum(w[c] for c in b.children)
        )
    return w


def _layout(
    trunk: Branch, w: dict[Branch, float], crown: float, base_width: float
) -> list[_Seg]:
    top = (0.0, TRUNK_HEIGHT)
    segs = [
        _curve(
            trunk, (0.0, 0.0), top, (_rng("trunk").random() - 0.5) * 0.12, base_width
        )
    ]
    stack = [(trunk, segs[0], -FAN, FAN)]
    while stack:
        b, seg, lo, hi = stack.pop()
        kids = sorted(b.children, key=lambda k: (k.node.created_at, k.node.uri))
        if not kids:
            continue
        # Oldest in the middle, newer ones alternating outward.
        row = kids[1::2][::-1] + kids[0::2]
        shares = [w[k] ** 0.6 for k in row]
        total = sum(shares)
        a = lo
        for k, share in zip(row, shares):
            r = _rng(k.key)
            slice_ = (hi - lo) * share / total
            ang = a + slice_ / 2 + (r.random() - 0.5) * 0.4 * slice_
            if len(kids) == 1 and b.node is None:
                ang = (0.4 + 0.4 * r.random()) * (1 if r.random() < 0.5 else -1)
            at = (0.55 if b.node is None else 0.4) + 0.45 * r.random()
            start = seg.at(at)
            # Reach outward from the top of the trunk, less with every fork.
            out = math.hypot(start[0] - top[0], start[1] - top[1])
            heft = 0.55 + 0.45 * min(1.0, math.log10(1 + w[k]) / 2)
            out += (
                crown * 0.32 * 0.78 ** (k.depth - 1) * (0.75 + 0.5 * r.random()) * heft
            )
            end = (top[0] + math.sin(ang) * out, top[1] + math.cos(ang) * out)
            # Never wider than the parent where it leaves it, or the joint bulges.
            width = min(
                base_width * math.sqrt(w[k] / w[trunk]),
                seg.width * (1 - TAPER * at) * 0.85,
            )
            child = _curve(k, start, end, (r.random() - 0.5) * 0.35, width)
            segs.append(child)
            stack.append((k, child, a, a + slice_))
            a += slice_
    return segs


def _sprigs(segs: list[_Seg], crown: float, firsts: list[tuple]) -> list[_Seg]:
    """Likes made straight on the seed have no repost to hang on, so they get
    twigs of their own, sprouting outward from the wood already there.

    `firsts` holds the (created_at, uri) of each twig's first like. A twig only
    grows from branches older than that, so reposts arriving later never move
    it onto someone else's branch."""
    top = segs[0].p1
    by_age = sorted(
        segs[1:], key=lambda s: (s.branch.node.created_at, s.branch.node.uri)
    )
    ages = [(s.branch.node.created_at, s.branch.node.uri) for s in by_age]
    twigs = []
    for i, first in enumerate(firsts):
        r = _rng(f"sprig/{i}")
        older = bisect.bisect_right(ages, first)
        if i == 0 or older == 0 or r.random() < 0.3:
            host = segs[0]
        else:
            host = by_age[r.randrange(older)]
        start = host.at(0.55 + 0.45 * r.random())
        dx, dy = start[0] - top[0], start[1] - top[1]
        out = math.atan2(dx, dy) if math.hypot(dx, dy) > 0.05 else 0.0
        ang = max(-FAN, min(FAN, out + (r.random() * 2 - 1) * 0.9))
        reach = crown * (0.18 + 0.27 * r.random())
        end = (start[0] + math.sin(ang) * reach, start[1] + math.cos(ang) * reach)
        twigs.append(_curve(segs[0].branch, start, end, (r.random() - 0.5) * 0.5, 0.0))
    return twigs


def _bundles(items: list) -> list[list]:
    """Twig k takes the next 2k + 1 likes, in the order they came: a new like
    joins the last twig or starts one, and nothing already drawn moves."""
    bundles, start, k = [], 0, 0
    while start < len(items):
        bundles.append(items[start : start + 2 * k + 1])
        start += 2 * k + 1
        k += 1
    return bundles


def _hls(
    hue: float, light: float, sat: float, alpha: int = 255
) -> tuple[int, int, int, int]:
    r, g, b = colorsys.hls_to_rgb(hue % 1, max(0, min(1, light)), max(0, min(1, sat)))
    return (round(r * 255), round(g * 255), round(b * 255), alpha)


def _mix(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _leaf(
    x: float, y: float, ang: float, length: float, width: float
) -> list[tuple[float, float]]:
    outline = []
    for i in range(9):
        s = i / 8
        outline.append((s * length, width / 2 * math.sin(math.pi * s) ** 0.85))
    for i in range(7, 0, -1):
        s = i / 8
        outline.append((s * length, -width / 2 * math.sin(math.pi * s) ** 0.85))
    ca, sa = math.cos(ang), math.sin(ang)
    return [(x + px * ca - py * sa, y + px * sa + py * ca) for px, py in outline]


@dataclass
class Rendered:
    image: Image.Image
    bbox: tuple[float, float, float, float]  # where the tree is, in image pixels

    def avatar(self, size: int = 800) -> Image.Image:
        """A square around the tree itself, so it reads even as a small circle."""
        x0, y0, x1, y1 = self.bbox
        w, h = self.image.size
        side = min(max(x1 - x0, y1 - y0) * 1.25, w, h)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        left = min(max(cx - side / 2, 0), w - side)
        top = min(max(cy - side / 2, 0), h - side)
        box = (round(left), round(top), round(left + side), round(top + side))
        return self.image.crop(box).resize((size, size), Image.LANCZOS)


def render(trunk: Branch, size: int = 1600) -> Rendered:
    w = _weights(trunk)
    n = sum(len(b.leaves) + len(b.flowers) + (b.node is not None) for b in trunk.walk())
    growth = math.log10(1 + n)
    crown = min(1.0, 0.35 + 0.22 * growth)
    segs = _layout(trunk, w, crown, 0.03 + 0.018 * growth)
    top = segs[0].p1

    # Each cluster is a curve, what grows along it, and its tint. A reposter's
    # leaves share a hue, so a branch reads as theirs.
    direct = [(x, False) for x in trunk.leaves] + [(x, True) for x in trunk.flowers]
    direct.sort(key=lambda item: (item[0].created_at, item[0].uri))
    bundles = _bundles(direct)
    firsts = [(bundle[0][0].created_at, bundle[0][0].uri) for bundle in bundles]
    twigs = _sprigs(segs, crown, firsts)
    clusters = [
        (twig, bundle, (0.27, 0.36, 0.42)) for twig, bundle in zip(twigs, bundles)
    ]
    for seg in segs[1:]:
        b, tint = seg.branch, _rng(seg.branch.node.actor_did)
        items = [(x, False) for x in b.leaves] + [(x, True) for x in b.flowers]
        clusters.append(
            (
                seg,
                items,
                (
                    0.2 + 0.2 * tint.random(),
                    0.3 + 0.14 * tint.random(),
                    0.35 + 0.25 * tint.random(),
                ),
            )
        )

    big = size * SS
    rim = [
        (top[0] + math.sin(a) * crown, top[1] + math.cos(a) * crown)
        for a in (-FAN, 0, FAN)
    ]
    points = [p for s in segs + twigs for p in (s.p0, s.c, s.p1)] + rim
    half_w = max(abs(p[0]) for p in points)
    height = max(p[1] for p in points)
    # A young tree is drawn small and grows into the frame as people join in.
    grow = min(0.7, 0.24 + 0.16 * growth)
    scale = min(big * grow / height, big * 0.4 / half_w)
    ground = big * 0.87

    def px(p: tuple[float, float]) -> tuple[float, float]:
        return (big / 2 + p[0] * scale, ground - p[1] * scale)

    img = Image.linear_gradient("L").resize((big, big))
    img = Image.composite(
        Image.new("RGB", (big, big), PAPER_BOTTOM),
        Image.new("RGB", (big, big), PAPER_TOP),
        img,
    )
    draw = ImageDraw.Draw(img, "RGBA")
    draw.ellipse(
        (-big * 0.35, ground - big * 0.035, big * 1.35, ground + big * 1.2), fill=HILL
    )
    shade = half_w * scale * 0.7
    draw.ellipse(
        (big / 2 - shade, ground - big * 0.012, big / 2 + shade, ground + big * 0.02),
        fill=SHADOW,
    )

    min_w = 1.5 * SS
    wood = [(s, s.branch.depth) for s in segs] + [(t, 1) for t in twigs]
    for seg, depth in sorted(wood, key=lambda item: item[1]):
        is_trunk = seg is segs[0]
        base = max(seg.width * scale, min_w)
        tip = max(base * (1 - TAPER), min_w)
        color = _mix(BARK, BARK_YOUNG, min(1.0, depth * 0.12))
        left, right = [], []
        for i in range(17):
            t = i / 16
            x, y = px(seg.at(t))
            tx, ty = seg.tangent(t)
            norm = math.hypot(tx, ty) or 1
            nx, ny = ty / norm, tx / norm  # the normal, with y flipped for the screen
            half = (base + (tip - base) * t) / 2
            if is_trunk:
                half *= 1 + 0.4 * (1 - t) ** 4  # the trunk flares into its roots
            left.append((x + nx * half, y + ny * half))
            right.append((x - nx * half, y - ny * half))
        draw.polygon(left + right[::-1], fill=color)
        # Round joints and tips; the trunk is left flat, a cap there reads as a knot.
        caps = [] if is_trunk else [(seg.p0, base / 2), (seg.p1, tip / 2)]
        for p, r in caps:
            x, y = px(p)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    leaf_px = max(9.0, 24 - 3.6 * growth) * SS
    marks: list[tuple[float, str, object]] = []  # (draw order, kind, payload)
    for seg, items, (hue, light, sat) in clusters:
        sigma = min(leaf_px * (0.6 + 0.4 * math.sqrt(len(items))), big * 0.1)
        for item, is_flower in items:
            r = _rng(item.uri)
            t = 0.3 + 0.7 * r.random() ** 0.6
            ax, ay = px(seg.at(t))
            dx, dy = (
                r.gauss(0, 1) * sigma * (0.4 + 0.6 * t),
                r.gauss(0, 1) * sigma * (0.4 + 0.6 * t),
            )
            ax, ay = ax + dx, ay + dy
            if is_flower:
                marks.append((2 + r.random(), "flower", (ax, ay, leaf_px * 0.6, r)))
                continue
            ang = math.atan2(dy, dx) + (r.random() - 0.5) * 1.6
            length = leaf_px * (0.8 + 0.5 * r.random())
            color = _hls(
                hue + (r.random() - 0.5) * 0.03,
                light + (r.random() - 0.5) * 0.12,
                sat,
                232,
            )
            marks.append(
                (
                    r.random(),
                    "leaf",
                    (
                        _leaf(ax, ay, ang, length, length * (0.42 + 0.12 * r.random())),
                        color,
                    ),
                )
            )

    if not trunk.leaves and not trunk.children:
        # A seedling: two first leaves so the empty tree is not just a stick.
        x, y = px(top)
        for side in (-1, 1):
            poly = _leaf(
                x, y, -math.pi / 2 + side * 0.95, leaf_px * 1.5, leaf_px * 0.75
            )
            marks.append((0, "leaf", (poly, _hls(0.27, 0.4, 0.45))))

    xs = [p[0] for s in segs + twigs for p in (px(s.p0), px(s.p1))]
    ys = [p[1] for s in segs + twigs for p in (px(s.p0), px(s.p1))]
    for _, kind, payload in sorted(marks, key=lambda m: m[0]):
        if kind == "leaf":
            poly, color = payload
            draw.polygon(poly, fill=color)
            xs.append(poly[8][0])
            ys.append(poly[8][1])
        else:
            x, y, rad, r = payload
            petal = PETALS[r.randrange(len(PETALS))]
            turn = r.random() * math.tau
            for i in range(5):
                a = turn + i * math.tau / 5
                cx, cy = x + math.cos(a) * rad * 0.6, y + math.sin(a) * rad * 0.6
                draw.ellipse(
                    (cx - rad * 0.5, cy - rad * 0.5, cx + rad * 0.5, cy + rad * 0.5),
                    fill=petal,
                )
            draw.ellipse(
                (x - rad * 0.32, y - rad * 0.32, x + rad * 0.32, y + rad * 0.32),
                fill=POLLEN,
            )
            xs.append(x)
            ys.append(y)

    image = img.resize((size, size), Image.LANCZOS)
    bbox = (min(xs) / SS, min(ys) / SS, max(xs) / SS, max(ys) / SS)
    return Rendered(image, bbox)


def extension(data: bytes) -> str:
    """ "jpg" or "png", for naming a file holding what encode() returned."""
    return "jpg" if data[:2] == b"\xff\xd8" else "png"


def encode(image: Image.Image, limit: int = UPLOAD_LIMIT) -> bytes:
    """PNG when it fits under Bluesky's limit, otherwise the best JPEG that does."""
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    if buf.tell() <= limit:
        return buf.getvalue()
    for quality in (92, 86, 80, 72, 64, 56):
        buf = io.BytesIO()
        image.convert("RGB").save(
            buf, "JPEG", quality=quality, optimize=True, progressive=True
        )
        if buf.tell() <= limit:
            return buf.getvalue()
    raise ValueError(f"image is still {buf.tell()} bytes at the lowest quality")
