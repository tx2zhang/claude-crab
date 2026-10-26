"""Vector crow character for the desktop pet.

The crab is pixel art; the crow is drawn procedurally with smooth vector
shapes. Every frame comes from a parametric pose (body bob, head tilt, wing
angle, beak, eyes, props), rendered at 4x and downsampled, so animations get
lots of smooth in-between frames instead of pixel-art steps.

Tk can only show binary transparency (the pet window uses a magenta colour
key), so each frame gets a thin outline ring and its alpha snapped to 0/255.
The ring hides the jaggies and gives the crow a clean sticker edge that
reads on both dark and light backgrounds.

Public API
    load_or_build(cache_dir) -> {anim_name: [PIL.Image RGBA, ...]}
    INTERVALS                -> {anim_name: ms per frame}
"""

import hashlib
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 128, 96          # design size (px at 100% display scaling)
OX = 6                  # shift all art right: room for the tail when leaning in
GROUND = 88             # y of the feet

# Output resolution follows the display scale (see configure). Shapes are
# authored in W x H design units and rasterised at SS x design size.
SS = 4                  # supersampling factor
OUT_W, OUT_H = W, H     # final frame size in device pixels
RING = 1                # outline ring thickness in device pixels


def configure(scale=1.0):
    """Render frames for a display scale (2.0 on a 200% display)."""
    global SS, OUT_W, OUT_H, RING
    scale = max(1.0, float(scale))
    SS = max(4, math.ceil(3 * scale))       # >= 3 samples per output pixel
    OUT_W, OUT_H = round(W * scale), round(H * scale)
    RING = max(1, round(scale))

# ---------- palette ----------
INK        = (14, 15, 20)
RIM        = (226, 230, 240)        # light sticker edge (visible on dark bg)
BODY_TOP   = (64, 68, 86)
BODY_BOT   = (19, 20, 26)
SHEEN      = (128, 150, 230)        # iridescent blue-violet
WING_TOP   = (58, 62, 80)
WING_BOT   = (24, 25, 33)
FAR_TOP    = (34, 36, 46)
FAR_BOT    = (17, 18, 23)
FEATHER_LN = (16, 17, 23)
TAIL_TOP   = (44, 47, 60)
TAIL_BOT   = (17, 18, 24)
BEAK_HI    = (104, 109, 126)
BEAK       = (60, 64, 77)
BEAK_DK    = (46, 49, 59)
BEAK_DK2   = (30, 32, 39)
BEAK_HL    = (160, 166, 184)
MOUTH      = (112, 38, 52)
TONGUE     = (220, 116, 128)
LEG        = (98, 102, 116)
LEG_DK     = (66, 69, 80)
EYE_W      = (250, 250, 246)
PUPIL      = (16, 16, 20)
LID        = (186, 190, 204)
CAP_TOP    = (200, 230, 252)
CAP_BOT    = (148, 196, 238)
CAP_BAND   = (250, 251, 255)
NEST_TOP   = (156, 110, 64)
NEST_BOT   = (92, 62, 36)
NEST_IN    = (58, 38, 22)
TWIGS      = [(182, 136, 84), (118, 82, 48), (210, 168, 110), (98, 66, 37)]
LID_TOP    = (96, 100, 113)
LID_BOT    = (64, 67, 77)
SCREEN     = (22, 24, 32)
DECK_TOP   = (204, 208, 218)
DECK_BOT   = (164, 169, 181)
DECK_EDGE  = (122, 126, 138)
KEY        = (82, 86, 98)
KEY_LIT    = (236, 132, 96)
KEY_GLOW   = (255, 222, 196)
CODE       = [(217, 119, 87), (214, 218, 228), (124, 208, 190), (154, 164, 196)]
ZZZ        = (228, 236, 255)
CONFETTI   = [(255, 110, 110), (255, 215, 80), (100, 180, 255),
              (130, 220, 130), (200, 130, 230), (255, 160, 90)]

_FONT_PATHS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
               "C:/Windows/Fonts/arial.ttf"]
_font_cache = {}


def _font(px):
    if px not in _font_cache:
        f = None
        for p in _FONT_PATHS:
            try:
                f = ImageFont.truetype(p, px)
                break
            except Exception:
                continue
        _font_cache[px] = f or ImageFont.load_default()
    return _font_cache[px]


# ---------- 2D affine helpers ----------
# m = (a, b, c, d, e, f):  x' = a*x + b*y + c,  y' = d*x + e*y + f

IDENT = (1, 0, 0, 0, 1, 0)


def _mul(m, n):
    """(m ∘ n)(p) == m(n(p))."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * D, a * B + b * E, a * C + b * F + c,
            d * A + e * D, d * B + e * E, d * C + e * F + f)


def chain(*ms):
    out = IDENT
    for m in ms:
        out = _mul(out, m)
    return out


def T(dx, dy):
    return (1, 0, dx, 0, 1, dy)


def R(deg, cx=0.0, cy=0.0):
    """Rotate about (cx, cy). Positive = clockwise on screen (y points down)."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return chain(T(cx, cy), (c, -s, 0, s, c, 0), T(-cx, -cy))


def S(sx, sy, cx=0.0, cy=0.0):
    return chain(T(cx, cy), (sx, 0, 0, 0, sy, 0), T(-cx, -cy))


def ap(m, p):
    a, b, c, d, e, f = m
    x, y = p
    return (a * x + b * y + c, d * x + e * y + f)


def tf(m, pts):
    return [ap(m, p) for p in pts]


# ---------- shape builders (local coords) ----------

def ell(cx, cy, rx, ry, deg=0.0, n=44):
    t0 = math.radians(deg)
    c0, s0 = math.cos(t0), math.sin(t0)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * c0 - y * s0, cy + x * s0 + y * c0))
    return pts


def capsule(p1, p2, r, n=12):
    (x1, y1), (x2, y2) = p1, p2
    a = math.atan2(y2 - y1, x2 - x1)
    pts = []
    for i in range(n + 1):
        t = a - math.pi / 2 + math.pi * i / n
        pts.append((x2 + r * math.cos(t), y2 + r * math.sin(t)))
    for i in range(n + 1):
        t = a + math.pi / 2 + math.pi * i / n
        pts.append((x1 + r * math.cos(t), y1 + r * math.sin(t)))
    return pts


def rrect(x0, y0, x1, y1, r, n=6):
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                       (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(n + 1):
            t = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def qbez(p0, p1, p2, n=14):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


# ---------- easing ----------

def ease(t):
    t = max(0.0, min(1.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def wave(t, k=1, ph=0.0):
    return math.sin(2 * math.pi * (k * t + ph))


def kf(t, keys):
    """Eased keyframe interpolation. keys = [(t, value), ...] sorted by t."""
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return v0 + (v1 - v0) * ease(u)
    return keys[-1][1]


# ---------- painter ----------

class Painter:
    def __init__(self):
        size = (W * SS, H * SS)
        self.img = Image.new("RGBA", size, (0, 0, 0, 0))
        self.fx = Image.new("RGBA", size, (0, 0, 0, 0))   # no outline (confetti, Zzz)
        self.d = ImageDraw.Draw(self.img)
        self.dfx = ImageDraw.Draw(self.fx)
        self.lw = 1.0          # line-width multiplier (crow scale)

    @staticmethod
    def _px(pts):
        return [((x + OX) * SS, y * SS) for x, y in pts]

    def poly(self, pts, fill, fx=False):
        (self.dfx if fx else self.d).polygon(self._px(pts), fill=fill)

    def line(self, pts, fill, width, fx=False):
        px = self._px(pts)
        d = self.dfx if fx else self.d
        w = width * self.lw * SS
        d.line(px, fill=fill, width=max(1, round(w)), joint="curve")
        r = w / 2
        for x, y in (px[0], px[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def gradient(self, shapes, top, bot, y0=None, y1=None):
        """Fill the union of world-space shapes with one vertical gradient.
        y0/y1 (logical px) pin the gradient span; default = shapes' bbox."""
        mask = Image.new("L", self.img.size, 0)
        md = ImageDraw.Draw(mask)
        for pts in shapes:
            md.polygon(self._px(pts), fill=255)
        bb = mask.getbbox()
        if not bb:
            return
        x0, by0, x1, by1 = bb
        g0 = by0 if y0 is None else y0 * SS
        g1 = by1 if y1 is None else y1 * SS
        span = max(1.0, g1 - g0)
        h = by1 - by0
        col = Image.new("RGB", (1, h))
        pix = col.load()
        for r in range(h):
            t = min(1.0, max(0.0, (by0 + r - g0) / span))
            pix[0, r] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        grad = col.resize((x1 - x0, h)).convert("RGBA")
        self.img.paste(grad, (x0, by0), mask.crop(bb))

    def sheen(self, pts, rgba, blur=0.0):
        """Soft translucent highlight, clipped to what's already drawn."""
        px = self._px(pts)
        pad = int(blur * SS * 3) + 2
        xs = [p[0] for p in px]
        ys = [p[1] for p in px]
        x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
        x1 = min(self.img.width, int(max(xs)) + pad)
        y1 = min(self.img.height, int(max(ys)) + pad)
        if x1 <= x0 or y1 <= y0:
            return
        layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([(x - x0, y - y0) for x, y in px], fill=rgba)
        if blur:
            layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
        region = self.img.crop((x0, y0, x1, y1))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), region.getchannel("A")))
        self.img.paste(Image.alpha_composite(region, layer), (x0, y0))
        self.d = ImageDraw.Draw(self.img)

    def text(self, x, y, s, size, fill, stroke=None):
        self.dfx.text(((x + OX) * SS, y * SS), s, font=_font(int(size * SS)), fill=fill,
                      anchor="mm", stroke_width=int(1.1 * SS) if stroke else 0,
                      stroke_fill=stroke)

    def finish(self):
        def down(im):
            return im.convert("RGBa").resize((OUT_W, OUT_H), Image.LANCZOS).convert("RGBA")

        body = down(self.img)
        solid = body.getchannel("A").point(lambda v: 255 if v > 24 else 0)
        ring = 2 * RING + 1
        ink_ring = solid.filter(ImageFilter.MaxFilter(ring))      # dark outline
        rim_ring = ink_ring.filter(ImageFilter.MaxFilter(ring))   # light rim outside it
        out = Image.new("RGBA", (OUT_W, OUT_H), RIM + (0,))
        out.putalpha(rim_ring)
        ink = Image.new("RGBA", (OUT_W, OUT_H), INK + (0,))
        ink.putalpha(ink_ring)
        out = Image.alpha_composite(out, ink)
        out = Image.alpha_composite(out, body)
        out = Image.alpha_composite(out, down(self.fx))
        out.putalpha(out.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        return out


# ---------- the crow ----------

HIP = (44.0, 80.0)          # body rotation pivot
NECK = (55.0, 51.0)         # head rotation pivot
SHOULDER = (50.0, 54.0)     # wing pivot
HINGE = (72.5, 41.2)        # beak hinge (head-local)

UPPER_BEAK = [(72.0, 34.4), (78.0, 35.0), (84.0, 36.8), (89.0, 39.0),
              (92.6, 41.4), (87.0, 41.7), (80.0, 41.6), (72.0, 41.4)]
LOWER_BEAK = [(72.0, 41.4), (80.0, 41.7), (87.0, 42.0), (90.0, 42.4),
              (85.5, 44.2), (78.0, 45.3), (72.0, 46.0)]


def pose(**kw):
    p = dict(bx=0.0, by=0.0, rot=0.0, sx=1.0, sy=1.0,
             head_dx=0.0, head_dy=0.0, head_rot=0.0,
             wing=0.0, far_wing=None, spread=0.0, tail=0.0,
             eye="open", look=(0.0, 0.0), beak=0.0,
             legs="stand", leg_swing=0.0,
             laptop=False, key=None, scroll=0.0,
             nest=False, nest_k=1.0, cap=False, cap_sway=0.0, cap_drop=0.0,
             zzz=None, confetti=None,
             k=1.0, perch=None, hat=False, cauldron=None)
    p.update(kw)
    return p


def _wing(pt, m, spread, far=False):
    """Folded wing pointing back from the shoulder; rotating it raises it,
    `spread` fans the primary feathers open."""
    shapes = [ell(-10.5, 8.5, 16.5, 9.2, 13)]
    prim = []
    for k in range(4):
        base = (-15.0 - k * 0.8, 9.5 + k * 2.4)
        ang = 180 - (10 + 9 * k) * (1 + spread * 1.4)
        length = 13.0 + spread * 4.0 - k * 0.8
        dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        c = (base[0] + dx * length / 2, base[1] + dy * length / 2)
        prim.append((base, (dx, dy), length))
        shapes.append(ell(c[0], c[1], length / 2 + 1.2, 3.3 - k * 0.25, ang))
    world = [tf(m, s) for s in shapes]
    if far:
        pt.gradient(world, FAR_TOP, FAR_BOT)
        return
    pt.gradient(world, WING_TOP, WING_BOT)
    for base, (dx, dy), length in prim[1:]:
        pt.line(tf(m, [base, (base[0] + dx * length * 0.85, base[1] + dy * length * 0.85)]),
                FEATHER_LN, 0.6)
    pt.line(tf(m, qbez((-1.5, 7.0), (-7.0, 12.5), (-14.5, 11.0))), FEATHER_LN, 0.6)
    pt.sheen(tf(m, ell(-7.5, 4.0, 10.0, 3.0, 12)), SHEEN + (70,), blur=1.2)


def _tail(pt, B, wag):
    m = chain(B, R(wag, 26, 68))
    shapes = [ell(14.5, 69.0, 13.0, 3.8, -16), ell(14.0, 72.5, 13.5, 3.8, -28),
              ell(16.0, 75.5, 11.5, 3.4, -42)]
    pt.gradient([tf(m, s) for s in shapes], TAIL_TOP, TAIL_BOT)
    pt.line(tf(m, [(25.0, 69.5), (5.0, 73.5)]), FEATHER_LN, 0.55)
    pt.line(tf(m, [(25.0, 72.0), (6.5, 79.0)]), FEATHER_LN, 0.55)


def _legs(pt, B, W0, k, P):
    mode = P["legs"]
    if mode == "none":
        return
    for (hx, fx_), col, far in (((41.0, 38.5), LEG_DK, True), ((49.0, 50.0), LEG, False)):
        hip = ap(B, (hx, 76.5))
        if mode == "dangle":
            ang = math.radians(P["leg_swing"] + (6 if far else -4))
            foot = (hip[0] + 12.5 * k * math.sin(ang), hip[1] + 12.5 * k * math.cos(ang))
            curl = 40
        else:
            foot = ap(W0, (fx_ + P["bx"], GROUND))
            if foot[1] - hip[1] > 13.0 * k:      # airborne: feet leave the ground
                foot = (hip[0] - k, hip[1] + 12.5 * k)
                curl = 25
            else:
                curl = 0
        pt.line([hip, foot], col, 1.9)
        toes = [(5.2, 0.4), (4.3, 2.3), (-3.3, 0.9)]
        for tx, ty in toes:
            a = math.radians(curl)
            rx, ry = tx * math.cos(a) - ty * math.sin(a), tx * math.sin(a) + ty * math.cos(a)
            pt.line([foot, (foot[0] + rx * k, foot[1] + ry * k)], col, 1.5)


def _eye(pt, Hd, state, look):
    c = (67.5, 37.2)
    if state in ("open", "wide"):
        rx, ry, pr = (5.4, 5.9, 3.3) if state == "open" else (6.4, 6.9, 2.3)
        pt.poly(tf(Hd, ell(c[0], c[1], rx, ry)), EYE_W)
        px, py = c[0] + 1.1 + look[0], c[1] + 0.5 + look[1]
        pt.poly(tf(Hd, ell(px, py, pr, pr)), PUPIL)
        pt.poly(tf(Hd, ell(px + 1.15, py - 1.35, 1.15, 1.15)), (255, 255, 255))
    elif state == "sleepy":                 # heavy lid: half-open, pupil sunk low
        pt.poly(tf(Hd, ell(c[0], c[1] + 1.6, 5.2, 3.0)), EYE_W)
        px = c[0] + 1.1 + look[0]
        pt.poly(tf(Hd, ell(px, c[1] + 2.1, 2.2, 2.2)), PUPIL)
        pt.line(tf(Hd, qbez((62.4, 37.6), (67.5, 35.0), (72.6, 37.6))), LID, 1.5)
    elif state == "closed":
        pt.line(tf(Hd, qbez((62.6, 37.2), (67.5, 41.4), (72.4, 37.2))), LID, 1.5)
    elif state == "happy":
        pt.line(tf(Hd, qbez((62.6, 39.8), (67.5, 33.2), (72.4, 39.8))), LID, 1.6)


def _beak(pt, Hd, open_):
    up = chain(Hd, R(-open_ * 13, *HINGE))
    lo = chain(Hd, R(open_ * 18, *HINGE))
    if open_ > 0.05:
        pt.poly([ap(up, (72.0, 41.4)), ap(up, (89.0, 41.6)),
                 ap(lo, (88.0, 42.2)), ap(lo, (72.0, 42.0))], MOUTH)
        pt.poly(tf(lo, ell(77.5, 42.6, 3.6, 1.4, 4)), TONGUE)
    pt.gradient([tf(lo, LOWER_BEAK)], BEAK_DK, BEAK_DK2)
    pt.gradient([tf(up, UPPER_BEAK)], BEAK_HI, BEAK)
    pt.line(tf(up, qbez((73.0, 35.5), (82.0, 36.6), (90.5, 40.8))), BEAK_HL, 0.55)


def _cap(pt, Hd, sway):
    def bend(p):
        x, y = p
        if x < 52:
            return ap(R(sway * (52 - x) / 10.0, 52, 22), p)
        return p
    body = [(73.6, 26.6), (72.6, 19.2), (66.5, 13.8), (58.0, 13.2), (50.5, 16.6),
            (44.5, 22.0), (40.2, 28.8), (43.2, 29.6), (47.6, 25.6), (51.0, 28.8)]
    pt.gradient([tf(Hd, [bend(p) for p in body])], CAP_TOP, CAP_BOT)
    pt.poly(tf(Hd, capsule((50.5, 29.6), (73.6, 26.8), 2.9)), CAP_BAND)
    pom = bend((39.4, 30.6))
    pt.poly(tf(Hd, ell(pom[0], pom[1], 3.7, 3.7)), CAP_BAND)


def _nest_m(nk):
    """Nest transform: nk < 1 shrinks it toward the floor so it can grow in."""
    return S(nk, nk, 44.0, GROUND - 2)


def _nest_back(pt, nk=1.0):
    m = _nest_m(nk)
    pt.gradient([tf(m, ell(44, 76.6, 31.5, 7.2))], NEST_TOP, NEST_BOT)
    pt.poly(tf(m, ell(44, 77.6, 28.0, 5.0)), NEST_IN)


def _nest_front(pt, nk=1.0):
    m, lw = _nest_m(nk), max(0.3, nk)
    rim = [(44 + 31.5 * math.cos(t), 76.6 + 7.2 * math.sin(t))
           for t in (math.pi * i / 30 for i in range(31))]
    bowl = [(44 + 33.5 * math.cos(t), 79.0 + 12.0 * math.sin(t))
            for t in (math.pi - math.pi * i / 30 for i in range(31))]
    pt.gradient([tf(m, rim + bowl)], NEST_TOP, NEST_BOT)
    rng = random.Random(11)
    for _ in range(30):
        x = rng.uniform(15, 71)
        u = (x - 44) / 31.5
        top = 76.6 + 7.2 * math.sqrt(max(0.0, 1 - u * u)) + 1.0
        v = (x - 44) / 33.5
        bot = 79.0 + 12.0 * math.sqrt(max(0.0, 1 - v * v)) - 1.2
        if bot - top < 1.5:
            continue
        y = rng.uniform(top, bot)
        dx = rng.uniform(7, 13) * rng.choice((-1, 1))
        dy = rng.uniform(-1.6, 1.6)
        pt.line(tf(m, qbez((x, y), (x + dx / 2, y + dy - 1.2), (x + dx, y + dy))),
                rng.choice(TWIGS), rng.uniform(0.7, 1.1) * lw)
    for (x0, y0), (x1, y1) in (((15, 77), (7, 73)), ((18, 79), (10, 80.5)),
                               ((72, 76.5), (81, 72.5)), ((70, 79.5), (79, 81))):
        pt.line(tf(m, [(x0, y0), (x1, y1)]), TWIGS[1], 0.9 * lw)


KEY_ROWS, KEY_COLS = 3, 9


def _key_quad(r, c, inset=0.13):
    def pt_uv(u, v):
        y = 78.0 + 7.0 * v
        xl = 84.0 + (80.0 - 84.0) * v
        xr = 118.0 + (119.5 - 118.0) * v
        return (xl + (xr - xl) * u, y)
    u0, u1 = (c + inset) / KEY_COLS, (c + 1 - inset) / KEY_COLS
    v0, v1 = (r + inset + 0.05) / KEY_ROWS, (r + 1 - inset) / KEY_ROWS
    return [pt_uv(u0, v0), pt_uv(u1, v0), pt_uv(u1, v1), pt_uv(u0, v1)]


CODE_LINES = [  # (indent, [(len, color_idx), ...]) — period 3 = pecks per loop
    (0, [(5, 0), (9, 1), (3, 3)]),
    (3, [(7, 1), (6, 2)]),
    (6, [(4, 0), (11, 1)]),
]


def _laptop(pt, P):
    pt.gradient([rrect(86.0, 54.0, 118.0, 78.0, 2.4)], LID_TOP, LID_BOT)
    x0, y0, x1, y1 = 88.2, 56.2, 115.8, 76.0
    pt.poly(rrect(x0, y0, x1, y1, 1.1), SCREEN)
    step = 3.0
    s = P["scroll"]
    base = int(math.floor(s))
    frac = s - base
    for i in range(-1, 9):
        y = y0 + 2.6 + (i - frac) * step
        if y < y0 + 1.4 or y > y1 - 1.6:
            continue
        indent, toks = CODE_LINES[(i + base) % len(CODE_LINES)]
        x = x0 + 2.2 + indent
        for ln, ci in toks:
            x_end = min(x + ln * 0.95, x1 - 2.0)
            if x_end > x + 0.5:
                pt.line([(x, y), (x_end, y)], CODE[ci], 1.15)
            x = x_end + 1.6
            if x > x1 - 3:
                break
    deck = [(84.0, 78.0), (118.0, 78.0), (119.5, 85.0), (80.0, 85.0)]
    pt.gradient([deck], DECK_TOP, DECK_BOT)
    pt.poly([(80.0, 85.0), (119.5, 85.0), (119.5, 87.2), (80.0, 87.2)], DECK_EDGE)
    for r in range(KEY_ROWS):
        if r == KEY_ROWS - 1:
            pt.poly(_key_quad(r, 0), KEY)
            pt.poly(_key_quad(r, 8), KEY)
            q0, q1 = _key_quad(r, 2), _key_quad(r, 6)
            pt.poly([q0[0], q1[1], q1[2], q0[3]], KEY)      # spacebar
            continue
        for c in range(KEY_COLS):
            pt.poly(_key_quad(r, c), KEY)
    if P["key"] is not None:
        r, c = P["key"]
        pt.poly(_key_quad(r, c, inset=0.02), KEY_LIT)
        pt.poly(_key_quad(r, c, inset=0.3), KEY_GLOW)


def _zzz(pt, t, Hd):
    ox, oy = ap(Hd, (68.0, 27.0))
    for k in range(3):
        u = (t + k / 3.0) % 1.0
        if u > 0.9:
            continue
        x = ox + 8 + u * 20 + 2.2 * math.sin(u * 2 * math.pi)
        y = oy - 4 - u * 24
        size = 6.0 + u * 7.0
        pt.text(x, y, "Z" if size > 9.5 else "z", size, ZZZ, stroke=INK)


def _confetti(pt, t):
    rng = random.Random(7)
    piece = [(-2.2, -1.0), (2.2, -1.0), (2.2, 1.0), (-2.2, 1.0)]
    for _ in range(18):
        x0 = rng.uniform(5, W - 5)
        speed = rng.choice((1, 1, 2))
        ph = rng.random()
        col = rng.choice(CONFETTI)
        spin = rng.choice((-2, -1, 1, 2))
        sway = rng.uniform(2, 5)
        y = ((t * speed + ph) % 1.0) * (H + 14) - 10
        x = x0 + sway * math.sin(2 * math.pi * (t * speed + ph))
        ang = 360 * (t * spin + ph)
        flip = max(0.25, abs(math.cos(math.radians(ang))))
        m = chain(T(x, y), R(ang * 0.5), S(flip, 1))
        pt.poly(tf(m, piece), col, fx=True)


HAT_TOP    = (112, 66, 160)
HAT_BOT    = (46, 24, 74)
HAT_BAND   = (26, 14, 36)
BUCKLE     = (240, 190, 80)


def _hat(pt, Hd):
    """Purple witch hat with a floppy tip and a gold buckle (head-local)."""
    cone = [(53.5, 27.5), (54.2, 18.5), (55.4, 11.5), (51.2, 6.0), (58.0, 6.6),
            (62.2, 10.6), (65.8, 17.0), (68.8, 25.0)]
    pt.gradient([tf(Hd, cone)], HAT_TOP, HAT_BOT)
    pt.poly(tf(Hd, [(53.7, 26.0), (68.4, 23.2), (67.7, 20.8), (54.0, 23.5)]), HAT_BAND)
    m = chain(Hd, T(61.2, 23.6), R(-11))
    pt.poly(tf(m, [(-1.9, -1.6), (1.9, -1.6), (1.9, 1.6), (-1.9, 1.6)]), BUCKLE)
    pt.poly(tf(m, [(-0.9, -0.7), (0.9, -0.7), (0.9, 0.7), (-0.9, 0.7)]), HAT_BAND)
    pt.gradient([tf(Hd, ell(61.0, 27.2, 15.5, 3.3, -12))], HAT_TOP, HAT_BOT)


# ---------- cauldron (Halloween brewing) ----------

POT_C = (78.0, 62.0)            # rim centre
POT_RX, POT_RY = 33.0, 7.0
LIQ_RX, LIQ_RY = 28.0, 5.0
PERCH = (51.0, 61.0)            # where the crow's feet sit on the rim
SUMMON = (97.0, 13.0)           # where items materialise
SPLASH_AT = (88.0, 62.8)

IRON_TOP  = (74, 76, 92)
IRON_BOT  = (16, 17, 22)
LIP_TOP   = (110, 114, 132)
LIP_BOT   = (46, 48, 58)
LIQ_TOP   = (200, 255, 150)
LIQ_BOT   = (64, 196, 76)
LIQ_HI    = (226, 255, 196)
GLOW_LINE = (150, 240, 140)
FIRE_OUT  = (255, 120, 28)
FIRE_IN   = (255, 216, 80)
LOG       = (112, 72, 42)
LOG_DK    = (72, 45, 26)
SPARK     = [(255, 243, 166), (205, 166, 255), (160, 255, 124)]
STEAM     = [(194, 248, 180), (216, 188, 255)]
DROP      = (141, 255, 106)

#           cx   w   h0  cycles/loop phase
FLAMES = [(67, 7, 8, 2, 0.3), (73, 8, 11, 3, 0.7), (79, 9, 13, 2, 0.0),
          (85, 8, 11, 3, 0.5), (91, 7, 8, 2, 0.2)]

SPOON    = (152, 102, 60)
SPOON_HI = (198, 148, 94)


def _flame(cx, base, w, h, lean):
    return [(cx - w / 2, base), (cx - w * 0.55, base - h * 0.35), (cx - w * 0.25, base - h * 0.75),
            (cx + lean, base - h), (cx + w * 0.3, base - h * 0.7), (cx + w * 0.55, base - h * 0.35),
            (cx + w / 2, base)]


def _arc(cx, cy, rx, ry, a0, a1, n=30):
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def _star(cx, cy, r, ang=0.0):
    pts = []
    for i in range(8):
        a = math.radians(ang + i * 45)
        rr = r if i % 2 == 0 else r * 0.38
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _cauldron_back(pt, C):
    """Fire, logs, feet, the rim, and the bubbling potion surface."""
    t = C["t"]
    pt.line([(59, 94.5), (97, 91.5)], LOG_DK, 3.2)
    pt.line([(59, 91.5), (97, 94.5)], LOG, 3.2)
    for cx, w, h0, m, ph in FLAMES:
        wob = math.sin(2 * math.pi * (m * t + ph))
        h = h0 * (0.82 + 0.22 * wob)
        lean = 1.6 * math.sin(2 * math.pi * (m * t + ph + 0.25))
        pt.poly(_flame(cx, 94.0, w, h, lean), FIRE_OUT)
        pt.poly(_flame(cx, 94.0, w * 0.55, h * 0.6, lean * 0.6), FIRE_IN)
    for x, y in ((56, 89.6), (78, 91.0), (100, 89.6)):
        pt.poly(ell(x, y, 4.2, 2.8), LIP_BOT)
    cx, cy = POT_C
    pt.gradient([ell(cx, cy, POT_RX, POT_RY)], LIP_TOP, LIP_BOT)
    pt.gradient([ell(cx, cy + 0.6, LIQ_RX, LIQ_RY)], LIQ_TOP, LIQ_BOT)
    a = C.get("stir", 2 * math.pi * t)                # swirl follows the spoon
    for j in range(2):                                 # slow swirl
        a0 = a + j * math.pi
        pts = [(cx + (5 + 15 * u) * math.cos(a0 + u * 3.0),
                cy + 0.6 + (1.0 + 2.8 * u) * math.sin(a0 + u * 3.0))
               for u in (i / 14 for i in range(15))]
        pt.line(pts, LIQ_HI, 0.7)
    rng = random.Random(5)
    for _ in range(7):                                 # bubbles grow and pop
        bx = rng.uniform(57, 100)
        by = cy + 0.6 + rng.uniform(-2.0, 2.6)
        m = rng.choice((1, 2))
        ph = rng.random()
        u = (t * m + ph) % 1.0
        if u < 0.7:
            r = 0.6 + 1.7 * (u / 0.7)
            pt.poly(ell(bx, by - r * 0.6, r, r), LIQ_HI)
            pt.poly(ell(bx - r * 0.3, by - r * 0.95, r * 0.32, r * 0.32), (255, 255, 255))
        elif u < 0.85:
            ring = ell(bx, by - 1.0, 2.8, 1.1)
            pt.line(ring + [ring[0]], LIQ_HI, 0.5)
    if C["splash"] is not None:                        # ripple from the drop
        p = C["splash"]
        ring = ell(SPLASH_AT[0], SPLASH_AT[1], 3 + 9 * p, 1.0 + 1.8 * p)
        pt.line(ring + [ring[0]], LIQ_HI, 0.8)


def _spoon(pt, C, grip):
    """Long wooden spoon from the crow's beak down into the potion; the end
    circles slowly with the stir angle."""
    th = C.get("stir", 0.0)
    ex = POT_C[0] + 4 + 7 * math.cos(th)
    ey = POT_C[1] + 0.6 + 1.7 * math.sin(th)
    gx, gy = grip
    tx, ty = gx + (gx - ex) * 0.22, gy + (gy - ey) * 0.22     # sticks up past the beak
    pt.line([(tx, ty), (ex, ey)], SPOON, 1.5)
    pt.line([(tx + 0.35, ty), (ex + 0.35, ey)], SPOON_HI, 0.45)
    ring = ell(ex, ey + 0.3, 3.2, 1.0)
    pt.line(ring + [ring[0]], LIQ_HI, 0.6)


def _cauldron_front(pt):
    """The iron belly and front lip, drawn over anything sinking in."""
    cx, cy = POT_C
    front = _arc(cx, cy, POT_RX, POT_RY, math.pi, 0)          # left → right via the front
    belly = _arc(cx, 75.0, 34.0, 14.0, 0, math.pi)            # right → left via the bottom
    pt.gradient([front + belly], IRON_TOP, IRON_BOT)
    lip = _arc(cx, cy + 0.8, POT_RX + 0.6, POT_RY + 1.7, math.pi, 0) + \
        _arc(cx, cy, POT_RX, POT_RY, 0, math.pi)
    pt.gradient([lip], LIP_TOP, LIP_BOT)
    pt.line(_arc(cx, cy, POT_RX - 2.0, POT_RY - 0.6, math.pi * 0.82, math.pi * 0.18),
            GLOW_LINE, 0.55)                                   # potion light on the lip
    pt.sheen(ell(64, 76, 10, 4.5, -18), (170, 180, 210, 70), blur=1.6)


def _item(pt, kind, cx, cy, s, ang):
    """Spooky ingredients: candy corn, eyeball, toadstool, bone."""
    m = chain(T(cx, cy), R(ang), S(s, s))
    if kind == "candy":
        apex, bl, br = (0.0, -6.5), (-5.0, 5.0), (5.0, 5.0)

        def tri(f):
            return [apex, (apex[0] + (bl[0] - apex[0]) * f, apex[1] + (bl[1] - apex[1]) * f),
                    (apex[0] + (br[0] - apex[0]) * f, apex[1] + (br[1] - apex[1]) * f)]
        pt.poly(tf(m, tri(1.0)), (255, 205, 70))
        pt.poly(tf(m, tri(0.68)), (255, 136, 38))
        pt.poly(tf(m, tri(0.34)), (252, 249, 238))
    elif kind == "eye":
        pt.poly(tf(m, ell(0, 0, 5.2, 5.2)), (248, 246, 240))
        pt.line(tf(m, qbez((-4.4, -1.8), (-3.0, -0.5), (-2.3, 0.6))), (220, 70, 70), 0.5)
        pt.line(tf(m, qbez((-3.6, 3.0), (-2.6, 2.2), (-2.0, 1.2))), (220, 70, 70), 0.5)
        pt.poly(tf(m, ell(1.3, 0, 2.7, 2.7)), (196, 54, 60))
        pt.poly(tf(m, ell(1.6, 0, 1.3, 1.3)), (18, 16, 20))
        pt.poly(tf(m, ell(2.3, -0.9, 0.6, 0.6)), (255, 255, 255))
    elif kind == "mushroom":
        pt.poly(tf(m, [(-2.0, 0.0), (2.0, 0.0), (2.4, 5.6), (-2.4, 5.6)]), (238, 224, 196))
        cap = [(6.2 * math.cos(a), -0.3 - 5.0 * math.sin(a))
               for a in (math.pi * i / 20 for i in range(21))]
        pt.poly(tf(m, cap), (214, 48, 62))
        for x, y, r in ((-2.6, -2.4, 1.1), (1.2, -3.6, 0.9), (3.4, -1.4, 0.8)):
            pt.poly(tf(m, ell(x, y, r, r)), (252, 244, 236))
    elif kind == "bone":
        pt.poly(tf(m, capsule((-5.0, 0.0), (5.0, 0.0), 1.6)), (240, 234, 220))
        for x in (-5.6, 5.6):
            for y in (-1.6, 1.6):
                pt.poly(tf(m, ell(x, y, 1.9, 1.9)), (240, 234, 220))


def _cauldron_fx(pt, C):
    """Steam, twinkles, the summoning sparkle ring, splash droplets."""
    t = C["t"]
    rng = random.Random(9)
    for i in range(6):
        x0 = rng.uniform(91, 106)      # clear of the crow's beak
        m = 1
        ph = rng.random()
        u = (t * m + ph) % 1.0
        if u > 0.9:
            continue
        x = x0 + 3 * math.sin(2 * math.pi * (u * 1.5 + ph))
        r = 2.6 * (1 - u) + 0.5
        pt.poly(ell(x, 58 - u * 34, r, r * 0.8), STEAM[i % 2], fx=True)
    for i, (x, y) in enumerate(((47, 50), (109, 53), (66, 46), (104, 40))):
        sv = math.sin(2 * math.pi * (t * 2 + i * 0.27))
        if sv > 0.2:
            pt.poly(_star(x, y, 1.0 + 1.6 * sv, 15 * i), SPARK[i % 3], fx=True)
    if C["cast"] > 0.02:
        cs = C["cast"]
        for j in range(6):
            a = 360 * t + j * 60
            r = 4 + 4 * cs
            x = SUMMON[0] + r * math.cos(math.radians(a))
            y = SUMMON[1] + r * 0.7 * math.sin(math.radians(a))
            pt.poly(_star(x, y, 0.8 + 1.4 * cs, a), SPARK[j % 3], fx=True)
    if C["splash"] is not None and C["splash"] < 0.99:
        p = C["splash"]
        rng = random.Random(3)
        for _ in range(5):
            a = math.radians(rng.uniform(-150, -30))
            v = rng.uniform(3.5, 6.5)
            x = SPLASH_AT[0] + math.cos(a) * v * p * 1.4
            y = SPLASH_AT[1] + math.sin(a) * v * p * 1.4 + 14 * p * p
            r = 1.3 * (1 - p) + 0.4
            pt.poly(ell(x, y, r, r), DROP, fx=True)


def draw_crow(P):
    pt = Painter()
    k = P["k"]
    px_, py_ = P["perch"] or (44.0, GROUND)
    W0 = chain(T(px_ - 44.0, py_ - GROUND), S(k, k, 44.0, GROUND))   # perch + scale
    B = chain(W0, T(P["bx"], P["by"]), R(P["rot"], *HIP), S(P["sx"], P["sy"], HIP[0], GROUND))
    Hd = chain(B, T(P["head_dx"], P["head_dy"]), R(P["head_rot"], *NECK))
    Wn = chain(B, T(*SHOULDER), R(P["wing"]))
    fw = P["far_wing"] if P["far_wing"] is not None else P["wing"] + 6
    Wf = chain(B, T(SHOULDER[0] - 4, SHOULDER[1] - 3), R(fw))

    if P["laptop"]:
        _laptop(pt, P)
    if P["nest"]:
        _nest_back(pt, P["nest_k"])
    C = P["cauldron"]
    if C is not None:
        _cauldron_back(pt, C)
        if C["item"] is not None:
            _item(pt, *C["item"])
        _spoon(pt, C, ap(Hd, (89.0, 41.5)))
        _cauldron_front(pt)
    pt.lw = k
    _wing(pt, Wf, P["spread"], far=True)
    _tail(pt, B, P["tail"])
    _legs(pt, B, W0, k, P)

    # Body, neck and head share one silhouette + one gradient so they read
    # as a single smooth shape.
    head_c = ap(Hd, (62.0, 40.0))
    sil = [tf(B, ell(44.0, 63.0, 23.0, 17.5, -16)), tf(B, ell(55.0, 57.0, 13.5, 13.5)),
           capsule(ap(B, (54.0, 56.0)), head_c, 9.5),
           tf(Hd, ell(62.0, 40.0, 14.0, 14.0)),
           tf(Hd, ell(57.5, 27.6, 5.0, 2.2, -35)), tf(Hd, ell(53.6, 29.4, 4.6, 2.0, -58))]
    top_y = ap(B, (44.0, 30.0))[1]
    bot_y = ap(B, (44.0, 82.0))[1]
    pt.gradient(sil, BODY_TOP, BODY_BOT, y0=top_y, y1=bot_y)
    pt.sheen(tf(B, ell(40.0, 53.0, 15.0, 6.0, -22)), SHEEN + (80,), blur=2.0)
    pt.sheen(tf(Hd, ell(58.0, 31.5, 8.0, 3.8, -25)), SHEEN + (85,), blur=1.5)

    if P["nest"]:
        _nest_front(pt, P["nest_k"])
    _wing(pt, Wn, P["spread"])
    _beak(pt, Hd, P["beak"])
    _eye(pt, Hd, P["eye"], P["look"])
    if P["cap"]:
        _cap(pt, chain(Hd, T(0, -P["cap_drop"])), P["cap_sway"])
    if P["hat"]:
        _hat(pt, Hd)
    pt.lw = 1.0
    if C is not None:
        _cauldron_fx(pt, C)
    if P["zzz"] is not None:
        _zzz(pt, P["zzz"], Hd)
    if P["confetti"] is not None:
        _confetti(pt, P["confetti"])
    return pt.finish()


# ---------- loose feathers (shake burst) ----------

def _feather(k, ang):
    """One loose crow feather, rotated by ang degrees. k = device px per
    design unit. Same sticker edge (ink + light rim) as the crow."""
    D = int(26 * k) + 6
    S = 4
    img = Image.new("RGBA", (D * S, D * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = D / 2
    t = math.radians(ang)
    ca, sa = math.cos(t), math.sin(t)

    def P(x, y):
        x, y = x * k, y * k
        return ((c + x * ca - y * sa) * S, (c + x * sa + y * ca) * S)

    left = [(0, -9.5), (-1.4, -8), (-2.8, -5), (-3.5, -1), (-3.2, 3), (-1.8, 6.2), (0, 7)]
    right = [(1.3, 6), (2.3, 3), (2.6, -1), (2.1, -5), (1.1, -8)]
    w = lambda v: max(1, round(v * k * S))
    d.polygon([P(*q) for q in left + right], fill=(30, 32, 42))
    d.polygon([P(*q) for q in left], fill=(52, 56, 74))                  # lit half
    for x0, y0, x1, y1 in ((-3.3, -2, -1.2, -3.4), (-2.6, 3.2, -0.8, 1.8), (2.4, 0.4, 0.8, -0.8)):
        d.line([P(x0, y0), P(x1, y1)], fill=(16, 17, 23), width=w(0.5))  # split barbs
    d.line([P(-1.6, -6.2), P(-2.3, 0)], fill=(116, 134, 210), width=w(0.7))  # sheen
    d.line([P(0, -9), P(0, 10)], fill=(156, 164, 190), width=w(0.55))       # quill
    small = img.convert("RGBa").resize((D, D), Image.LANCZOS).convert("RGBA")
    solid = small.getchannel("A").point(lambda v: 255 if v > 24 else 0)
    ink_ring = solid.filter(ImageFilter.MaxFilter(3))
    rim_ring = ink_ring.filter(ImageFilter.MaxFilter(3))
    out = Image.new("RGBA", (D, D), RIM + (0,))
    out.putalpha(rim_ring)
    ink = Image.new("RGBA", (D, D), INK + (0,))
    ink.putalpha(ink_ring)
    out = Image.alpha_composite(Image.alpha_composite(out, ink), small)
    out.putalpha(out.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
    return out


FEATHER_ANGLES = 24


def feather_frames(scale=1.0):
    """{size: [PIL image per 15° of rotation]} for the shake-burst feathers."""
    return {sz: [_feather(scale * sz, 360 * i / FEATHER_ANGLES) for i in range(FEATHER_ANGLES)]
            for sz in (1.0, 0.75)}


# ---------- animations ----------

def anim_idle(n=30):
    """Alert stance: breathing, curious head tilts, a blink."""
    out = []
    for f in range(n):
        t = f / n
        br = wave(t, 2)
        tilt = kf(t, [(0, 0), (0.12, 0), (0.24, -13), (0.42, -13), (0.52, 0),
                      (0.62, 0), (0.72, 9), (0.86, 9), (0.96, 0), (1, 0)])
        eye = "closed" if (0.46 <= t < 0.5 or 0.9 <= t < 0.934) else "open"
        out.append(pose(by=-0.5 * br, sy=1 + 0.012 * br, head_rot=tilt,
                        head_dy=0.4 * br, eye=eye,
                        look=(0.7 * math.sin(2 * math.pi * t), -0.2),
                        wing=1.5 + 1.5 * br, tail=1.5 * wave(t, 1)))
    return out


KEY_SEQ = [(1, 1), (0, 2), (1, 0)]
# Head poses (body rot, head rot, head dx, head dy). CONTACT puts the beak tip
# on the keys; HOVER sits just above them; THINK is head up, eyeing the screen.
THINK = (2.8, 19.0, 1.9, 1.6)      # only half-raised: a calm glance at the screen
HOVER = (3.5, 41.0, 3.3, 3.2)
CONTACT = (4.0, 48.0, 4.0, 4.0)
TAP = [0.0, 0.55, 1.0, 0.6, 0.2, 0.0]   # one small, quick peck (6 frames)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def anim_typing():
    """Gentle typing: lower the head, peck peck peck (each lights a key and
    scrolls the code a line), lift up and ponder the screen, repeat."""
    out = []
    lower, taps, lift, think = 6, 18, 6, 12
    n = lower + taps + lift + think
    for f in range(n):
        key, look, eye, ponder = None, (1.3, 1.5), "open", 0.0
        if f < lower:                                   # settle down to the keys
            head = _mix(THINK, HOVER, ease(f / (lower - 1)))
            scroll = 0.0
        elif f < lower + taps:                          # peck peck peck
            k, c = divmod(f - lower, len(TAP))
            head = _mix(HOVER, CONTACT, TAP[c])
            if c in (2, 3):
                key = KEY_SEQ[k]
            scroll = k + (ease((c - 2) / 3.0) if c >= 2 else 0.0)
        elif f < lower + taps + lift:                   # lift up
            u = (f - lower - taps) / (lift - 1)
            head = _mix(HOVER, THINK, ease(u))
            scroll = 3.0
            look = (1.3 + 0.2 * u, 1.5 - 2.0 * u)
        else:                                           # stop and think
            u = (f - lower - taps - lift) / (think - 1)
            head = THINK
            ponder = -3.0 * math.sin(math.pi * u)       # slow thoughtful tilt
            eye = "closed" if 0.42 <= u < 0.55 else "open"
            look = (1.5, -0.5)
            scroll = 3.0
        rot, hr, dx, dy = head
        out.append(pose(bx=-3.0, rot=rot, head_rot=hr + ponder, head_dx=dx,
                        head_dy=dy, wing=3.0, tail=-1.5 * math.sin(2 * math.pi * f / n),
                        eye=eye, look=look, laptop=True, key=key, scroll=scroll))
    return out


def anim_sleeping(n=36):
    """Tucked into a nest with a nightcap; slow breathing; Zzz drift up."""
    out = []
    for f in range(n):
        t = f / n
        b = wave(t, 1)
        out.append(pose(nest=True, legs="none", by=11 - 0.8 * b,
                        sy=1.0 + 0.03 * b, sx=1.0 - 0.01 * b,
                        head_rot=22 + 2.5 * b, head_dy=6 + 0.6 * b, head_dx=-2,
                        eye="closed", cap=True, cap_sway=4 * wave(t, 1, 0.25),
                        zzz=t, wing=-2, tail=10))
    return out


def anim_settle(n=46):
    """One-shot wind-down into the sleeping loop: a big yawn and stretch,
    the nest grows in under it, it sinks in with heavy eyes and tucks its
    head, the nightcap drops on with a little bounce, one cosy wiggle.
    The last frame is the sleeping loop's first, so the hand-off is seamless."""
    out = []
    for f in range(n):
        t = f / (n - 1)
        yawn = kf(t, [(0, 0), (0.06, 0), (0.16, 0.8), (0.26, 0.8), (0.34, 0), (1, 0)])
        stretch = kf(t, [(0, 0), (0.08, 0), (0.18, 1), (0.28, 1), (0.36, 0), (1, 0)])
        nk = kf(t, [(0, 0), (0.2, 0), (0.36, 1.06), (0.42, 1.0), (1, 1.0)])
        sink = kf(t, [(0, 0), (0.36, 0), (0.62, 1), (1, 1)])
        drop = kf(t, [(0, 22), (0.66, 22), (0.78, 0), (0.83, 2.2), (0.88, 0), (1, 0)])
        wig = math.sin(math.pi * max(0.0, (t - 0.86) / 0.14) * 2) * (1 - ease((t - 0.86) / 0.14)) \
            if t > 0.86 else 0.0
        if 0.08 <= t < 0.34:
            eye = "closed"                      # eyes squeezed shut for the yawn
        elif t < 0.4:
            eye = "open"
        elif t < 0.58:
            eye = "sleepy"
        else:
            eye = "closed"
        out.append(pose(
            nest=t >= 0.2, nest_k=max(0.02, nk),
            legs="stand" if sink < 0.45 else "none",
            by=11 * sink + 0.6 * wig, rot=1.2 * wig,
            sy=1.0 + 0.04 * stretch - 0.04 * math.sin(math.pi * sink) + 0.015 * wig,
            sx=1.0 - 0.015 * stretch + 0.03 * math.sin(math.pi * sink),
            head_rot=-14 * stretch + 22 * sink, head_dy=6 * sink, head_dx=-2 * sink,
            beak=yawn, eye=eye, look=(0.4, 0.8 * sink),
            wing=1.5 + 14 * stretch - 3.5 * sink, spread=0.25 * stretch,
            tail=4 * stretch + 10 * sink,
            cap=t >= 0.66, cap_drop=drop, cap_sway=4 * min(1.0, max(0.0, (t - 0.8) / 0.2))))
    return out


def anim_cheer(n=16):
    """Hop + flap + caw with happy eyes and confetti."""
    out = []
    for f in range(n):
        t = f / n
        if t < 0.12:
            y, sq = 0.0, 1 - 0.10 * ease(t / 0.12)
        elif t < 0.72:
            u = (t - 0.12) / 0.6
            y, sq = -15 * math.sin(math.pi * u), 1 + 0.05 * math.sin(math.pi * u)
        elif t < 0.86:
            u = (t - 0.72) / 0.14
            y, sq = 0.0, 1 - 0.11 * math.sin(math.pi * u)
        else:
            y, sq = 0.0, 1.0
        flap = wave(t, 3)
        out.append(pose(by=y, sy=sq, sx=1 + (1 - sq) * 0.6,
                        wing=52 + 40 * flap, far_wing=62 + 40 * flap,
                        spread=0.65 + 0.35 * flap, eye="happy",
                        beak=0.55 + 0.45 * max(0.0, wave(t, 2)),
                        head_rot=-12 + 4 * wave(t, 2), tail=-9 * flap, confetti=t))
    return out


def anim_dangle(n=12):
    """Picked up: frantic flapping, wide eyes, legs swinging."""
    out = []
    for f in range(n):
        t = f / n
        w = wave(t, 2)
        out.append(pose(rot=-5 + 4 * wave(t, 1), by=-3,
                        wing=62 + 52 * w, far_wing=74 + 52 * wave(t, 2, 0.06),
                        spread=0.7 + 0.3 * w, legs="dangle",
                        leg_swing=16 * wave(t, 1, 0.25), eye="wide", look=(0, 1.0),
                        beak=0.35 + 0.1 * w, head_rot=-6 + 3 * w, tail=8 * wave(t, 1)))
    return out


def anim_hop(n=8):
    """Quick hop to attention when work starts."""
    ys = [0, 2, -8, -12, -9, -3, 1, 0]
    sq = [1, 0.92, 1.05, 1.04, 1.03, 1.0, 0.94, 1]
    wg = [0, 0, 40, 62, 42, 16, 5, 0]
    return [pose(by=ys[f], sy=sq[f], sx=1 + (1 - sq[f]) * 0.6, wing=wg[f],
                 far_wing=wg[f] + 6, spread=wg[f] / 120.0, head_rot=-6 if 2 <= f <= 4 else 0,
                 eye="open") for f in range(n)]


ITEMS = ["candy", "eye", "mushroom", "bone"]


AMB = 30      # ambient period in frames: fire/bubbles/steam repeat every 2.4s
STIR = 60     # one slow turn of the spoon (4.8s) before each ingredient


def _brew_pose(amb_f, stir=0.0, stirring=False, item=None, cast=0.0, splash=None,
               follow=0.0, blink=False):
    a = amb_f / AMB
    breath = math.sin(2 * math.pi * a)
    if stirring:              # tiny sway with the spoon, eyes on the swirl
        head, rot = 3 + 2 * math.sin(stir), 4 + 0.6 * math.sin(stir)
        look = (1.2 + 0.6 * math.cos(stir), 1.5)
    else:                     # eyes follow the ingredient, a small nod
        head, rot = 1 + 5 * follow, 4
        look = (1.6, -1.8 + 3.4 * follow)
    return pose(k=0.7, perch=PERCH, rot=rot, hat=True,
                by=-0.3 * breath, sy=1 + 0.008 * breath,
                wing=6, far_wing=10, head_rot=head,
                eye="closed" if blink else "open", look=look, tail=0.8 * breath,
                cauldron=dict(t=a, item=item, cast=cast, splash=splash, stir=stir))


def anim_brewing():
    """Calm brewing loop (~29s): the crow slowly stirs the potion with a
    wooden spoon for 2/3 of the time, then conjures one ingredient that
    fades in, hovers, drifts into the pot and splashes softly. Four rounds
    (candy corn, eyeball, toadstool, bone). The stir section is identical
    every round, so build_frames renders it once and reuses the frames."""
    out = []
    for kind in ITEMS:
        for f in range(STIR):                                  # stir: one slow turn
            out.append(_brew_pose(f % AMB, stir=2 * math.pi * f / STIR,
                                  stirring=True, blink=f in (40, 41)))
        for c in range(AMB):                                   # one ingredient
            item, splash, cast, follow = None, None, 0.0, 0.0
            if c < 6:
                u = ease(c / 5)
                cast = u
                item = (kind, SUMMON[0], SUMMON[1], 1.3 * (0.3 + 0.7 * u), 0)
            elif c < 12:
                u = (c - 6) / 6
                cast = 1 - u
                item = (kind, SUMMON[0], SUMMON[1] + 0.8 * math.sin(u * math.pi), 1.3, 0)
            elif c < 24:
                u = ease((c - 12) / 11)
                item = (kind, SUMMON[0] + (SPLASH_AT[0] - SUMMON[0]) * u,
                        SUMMON[1] + (SPLASH_AT[1] - 2 - SUMMON[1]) * u, 1.3, 50 * u)
                follow = u
            elif c < 28:
                splash = (c - 23) / 5
                follow = 1.0
            else:
                follow = 1.0 - (c - 27) / 3
            out.append(_brew_pose(c, item=item, cast=cast, splash=splash, follow=follow))
    return out


BUILDERS = {
    "idle": anim_idle,
    "typing": anim_typing,
    "brewing": anim_brewing,
    "sleeping": anim_sleeping,
    "settle": anim_settle,
    "cheer": anim_cheer,
    "dangle": anim_dangle,
    "hop": anim_hop,
}

INTERVALS = {
    "standing": 80,
    "typing": 65,
    "brewing": 80,
    "sleeping": 90,
    "settle": 70,
    "cheer": 60,
    "dangle": 45,
    "hop": 50,
}


def build_frames(scale=1.0):
    """{anim: [PIL frames]}. Identical poses (e.g. the repeated stir) are
    rendered once and the same Image object is reused in the sequence."""
    configure(scale)
    out = {}
    for name, fn in BUILDERS.items():
        memo, seq = {}, []
        for pz in fn():
            key = json.dumps(pz, sort_keys=True, default=str)
            if key not in memo:
                memo[key] = draw_crow(pz)
            seq.append(memo[key])
        out[name] = seq
    return out


def _source_version():
    return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]


def load_or_build(cache_dir, scale=1.0):
    """Frames from the disk cache if it matches this file's source, else
    render fresh (a couple of seconds) and refresh the cache."""
    cache_dir = Path(cache_dir)
    manifest = cache_dir / "manifest.json"
    version = f"{_source_version()}@{float(scale):g}"
    try:
        meta = json.loads(manifest.read_text())
        if meta.get("version") == version:
            out = {}
            for name, seq in meta["seq"].items():
                uniq = []
                for i in range(max(seq) + 1):
                    with Image.open(cache_dir / f"{name}_{i:03d}.png") as im:
                        uniq.append(im.convert("RGBA"))
                out[name] = [uniq[j] for j in seq]
            return out
    except Exception:
        pass
    frames = build_frames(scale)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for old in cache_dir.glob("*.png"):
            old.unlink()
        seqs = {}
        for name, imgs in frames.items():
            index, seq = {}, []
            for im in imgs:
                if id(im) not in index:
                    index[id(im)] = len(index)
                    im.save(cache_dir / f"{name}_{index[id(im)]:03d}.png")
                seq.append(index[id(im)])
            seqs[name] = seq
        manifest.write_text(json.dumps({"version": version, "seq": seqs}))
    except Exception:
        pass
    return frames
