"""Witchy chrome for the session notification card (crow cauldron mode).

Draws the card body, a double border (pumpkin-orange outer, violet inner),
cobwebs in the top corners, bats on the top edge, a spider dangling off the
bottom, gold sparkles, and the tail pointing at the pet. Rendered with PIL
at 3x and downsampled for smooth edges; the outer silhouette gets a 1px ink
ring and binary alpha so it sits cleanly on Tk's transparent window.
"""

import math

from PIL import Image, ImageDraw, ImageFilter

FILL = (26, 19, 37)            # card body; rows use the same colour
HOVER = (42, 31, 58)
DIVIDER = (46, 35, 66)
ORANGE = (255, 138, 43)
VIOLET = (139, 92, 246)
WEB = (205, 191, 234)
BAT = (52, 34, 74)
BAT_EYE = (255, 170, 60)
SPIDER = (58, 38, 82)          # tinted like the bats so it shows on dark desktops
GOLD = (255, 214, 128)
INK = (8, 6, 12)

# Status rows: witchy copy + colours.
STATUS = {
    "working":    ("Brewing", "#ff8c1a"),
    "tool":       ("Conjuring", "#ff8c1a"),
    "ready":      ("Ready", "#7cfc6a"),
    "finished":   ("Done", "#7cfc6a"),
    "attention":  ("Needs you", "#ff4d6d"),
    "permission": ("Approve?", "#ffb347"),
    "idle":       ("Idle", "#8f84a6"),
}


def margins(u):
    """Extra room around the card for ornaments: (side, top, bottom extra)."""
    return round(4 * u), round(15 * u), round(14 * u)


def _bat(d, cx, cy, s, S):
    """Bat silhouette with spread scalloped wings, centred at (cx, cy)."""
    def P(x, y):
        return ((cx + x * s) * S, (cy + y * s) * S)
    for side in (1, -1):
        wing = [(1.0, -0.8), (3.6, -2.8), (6.6, -2.4), (8.6, -0.2), (7.0, -0.4),
                (5.8, 1.2), (4.4, 0.0), (3.1, 1.3), (1.6, 0.5)]
        d.polygon([P(side * x, y) for x, y in wing], fill=BAT)
    d.ellipse([P(-1.5, -2.1), P(1.5, 2.3)], fill=BAT)
    d.polygon([P(-1.3, -1.6), P(-0.9, -3.4), P(-0.3, -1.8)], fill=BAT)    # ears
    d.polygon([P(1.3, -1.6), P(0.9, -3.4), P(0.3, -1.8)], fill=BAT)
    for ex in (-0.55, 0.55):
        d.ellipse([P(ex - 0.32, -1.1), P(ex + 0.32, -0.46)], fill=BAT_EYE)


def _cobweb(d, cx, cy, dx, dy, L, w, S):
    """Corner web: threads fanning from (cx, cy) into the card, with sagging
    rungs. dx/dy = +1/-1 pick the direction into the card."""
    angles = [math.radians(a) for a in (0, 22, 45, 68, 90)]
    rays = [(math.cos(a) * dx, math.sin(a) * dy) for a in angles]
    for rx, ry in rays:
        d.line([(cx * S, cy * S), ((cx + rx * L) * S, (cy + ry * L) * S)], fill=WEB, width=w)
    for frac in (0.36, 0.64, 0.92):
        r = L * frac
        for (ax, ay), (bx, by) in zip(rays, rays[1:]):
            p0 = (cx + ax * r, cy + ay * r)
            p2 = (cx + bx * r, cy + by * r)
            mid = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2)
            p1 = (mid[0] - (mid[0] - cx) * 0.18, mid[1] - (mid[1] - cy) * 0.18)  # sag in
            pts = []
            for i in range(9):
                t = i / 8
                q = 1 - t
                pts.append(((q * q * p0[0] + 2 * q * t * p1[0] + t * t * p2[0]) * S,
                            (q * q * p0[1] + 2 * q * t * p1[1] + t * t * p2[1]) * S))
            d.line(pts, fill=WEB, width=w)


def _spider(d, cx, top, thread, s, S, w):
    """Spider dangling on a thread from (cx, top)."""
    by = top + thread
    d.line([(cx * S, top * S), (cx * S, by * S)], fill=WEB, width=w)
    for side in (1, -1):
        for k, (a, b) in enumerate(((1.0, -1.6), (1.6, -0.3), (1.7, 1.0), (1.2, 2.2))):
            knee = (cx + side * (a + 0.9) * s, by + (b - 0.9) * s)
            foot = (cx + side * (a + 1.6) * s, by + (b + 0.6) * s)
            d.line([(cx * S, (by + 0.6 * s) * S), (knee[0] * S, knee[1] * S),
                    (foot[0] * S, foot[1] * S)], fill=SPIDER, width=w)
    d.ellipse([(cx - 1.9 * s) * S, (by - 0.4 * s) * S, (cx + 1.9 * s) * S, (by + 3.4 * s) * S],
              fill=SPIDER)
    d.ellipse([(cx - 1.1 * s) * S, (by - 1.6 * s) * S, (cx + 1.1 * s) * S, (by + 0.4 * s) * S],
              fill=SPIDER)
    for ex in (-0.45, 0.45):
        d.ellipse([(cx + (ex - 0.28) * s) * S, (by - 0.95 * s) * S,
                   (cx + (ex + 0.28) * s) * S, (by - 0.39 * s) * S], fill=(255, 255, 255))


def _sparkle(d, cx, cy, r, S):
    pts = []
    for i in range(8):
        a = math.radians(i * 45)
        rr = r if i % 2 == 0 else r * 0.36
        pts.append(((cx + rr * math.cos(a)) * S, (cy + rr * math.sin(a)) * S))
    d.polygon(pts, fill=GOLD)


def card_chrome(cw, ch, tail_x, tail_w, tail_h, radius, u):
    """Full chrome image for a card of cw x ch device px. The card sits at
    (mx, mt) inside the image; tail_x is measured from the card's left."""
    mx, mt, mb_extra = margins(u)
    W, H = cw + 2 * mx, mt + ch + tail_h + mb_extra
    S = 3
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = mx, mt, mx + cw, mt + ch
    lw = max(1, round(1.6 * u * S))
    thin = max(1, round(0.8 * u * S))

    d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=radius * S,
                        fill=FILL, outline=ORANGE, width=lw)
    tx, hw = x0 + tail_x, tail_w / 2
    d.polygon([((tx - hw) * S, (y1 - 1.5 * u) * S), ((tx + hw) * S, (y1 - 1.5 * u) * S),
               (tx * S, (y1 + tail_h) * S)], fill=FILL)
    d.line([((tx - hw) * S, (y1 - 0.6 * u) * S), (tx * S, (y1 + tail_h) * S),
            ((tx + hw) * S, (y1 - 0.6 * u) * S)], fill=ORANGE, width=lw, joint="curve")
    inset = 4 * u
    d.rounded_rectangle([(x0 + inset) * S, (y0 + inset) * S, (x1 - inset) * S, (y1 - inset) * S],
                        radius=max(1, radius - inset) * S, outline=VIOLET, width=thin)

    web = 21 * u
    _cobweb(d, x0 + 2.2 * u, y0 + 2.2 * u, 1, 1, web, thin, S)
    _cobweb(d, x1 - 2.2 * u, y0 + 2.2 * u, -1, 1, web, thin, S)
    _sparkle(d, x0 + 7 * u, y1 - 6.5 * u, 2.6 * u, S)
    _sparkle(d, x1 - 7 * u, y1 - 6.5 * u, 2.6 * u, S)
    _bat(d, x0 + cw * 0.24, y0 - 1.4 * u, 1.95 * u, S)          # perched on the edge
    _bat(d, x0 + cw * 0.76, y0 - 8.0 * u, 1.4 * u, S)          # fluttering above
    _spider(d, x0 + cw * 0.17, y1, 6 * u, 1.55 * u, S, thin)    # dangling below

    small = img.convert("RGBa").resize((W, H), Image.LANCZOS).convert("RGBA")
    solid = small.getchannel("A").point(lambda v: 255 if v > 24 else 0)
    ring = solid.filter(ImageFilter.MaxFilter(3))
    out = Image.new("RGBA", (W, H), INK + (0,))
    out.putalpha(ring)
    out = Image.alpha_composite(out, small)
    out.putalpha(out.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
    return out, (mx, mt)
