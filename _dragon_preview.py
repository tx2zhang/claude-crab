"""Render 4 detailed-ish dragon designs to one preview, with 3-shade shading."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from PIL import Image, ImageDraw, ImageFont
from pet import crab_image, PALETTE

# Shading palette additions
PALETTE.update({
    # Green dragon (forest) — light / mid / dark / darkest
    "n": (165, 225, 160, 255),
    "N": (95, 180, 110, 255),
    "j": (50, 120, 75, 255),
    "J": (28, 75, 50, 255),
    # Blue dragon (sky) — light / mid / dark / darkest
    "c": (155, 210, 250, 255),
    "C": (75, 145, 225, 255),
    "v": (35, 85, 170, 255),
    "V": (20, 50, 115, 255),
    # Red dragon (crimson)
    "f": (245, 155, 120, 255),
    "F": (215, 80, 70, 255),
    "q": (150, 40, 50, 255),
    "Q": (90, 25, 35, 255),
    # Purple dragon (twilight)
    "u": (220, 180, 245, 255),
    "U": (160, 115, 215, 255),
    "i": (105, 60, 165, 255),
    "I": (55, 35, 100, 255),
    # Accents
    "T": (250, 245, 220, 255),  # claw / tooth
    "e": (28, 18, 22, 255),     # eye
    "Y": (245, 205, 80, 255),   # whisker / underglow
    "M": (225, 185, 95, 255),   # horn light
    "m": (165, 125, 50, 255),   # horn dark
})

# --- Design 1: Forest Western (green side profile, folded wings) ---
DRAGON_1 = [
    "........................",
    "..mM.............mM.....",
    "..MM.............MM.....",
    ".jJnnj...........jJnnj..",
    "jJnNNNj.........jJnNNNj.",
    "jJnNeNNj.......jJnNeNNj.",
    "jJnNNNNNj.....jJnNNNNNj.",
    ".jJNNNNNNNNNNNNJNNNNNj..",
    "..jJNNNNNNNNNNNJNNNNj...",
    "...jJJNNNNNNNNJjJNNJj...",
    "....jJJNNNNNNNJjjJNJj...",
    ".....jJJJNNNNNJjjjJj....",
    "......jJJJNNNJJjjjj.....",
    "........jJJJJJJjj.......",
    ".........jJJJJj.........",
    ".........NN..NN.........",
    ".........NN..NN.........",
    ".........TT..TT.........",
]

# --- Design 2: Sky Serpent (blue eastern, coiled) ---
DRAGON_2 = [
    "........................",
    "........MM.....MM.......",
    ".......mMM.....MMm......",
    "......vCCCCCCCCCCv......",
    ".....vCCccccccccCCv.....",
    ".....vCceCCCCCeCCCv.....",
    ".....vCCccCCCccCCCv.....",
    "......vCCCccccCCCv......",
    "..YYY..vvCCCCCvv..YYY...",
    "...YY..vCCCCCCCv..YY....",
    "......vvCCcccCCvv.......",
    ".....vCCCccccCCCv.......",
    "....vCCcccvvccccCv......",
    "....vCccvvVVvccccCv.....",
    "....vCccvVVVvccccCv.....",
    ".....vCcccvvvccccCv.....",
    "......vCCCccccCCCv......",
    "........vCCCCCv.........",
]

# --- Design 3: Crimson Spread (red western, front view, wings open) ---
DRAGON_3 = [
    "........MM.....MM.......",
    ".......mMM.....MMm......",
    "......qFFFFFFFFFFq......",
    ".....qFFFFFFFFFFFFq.....",
    ".....qFFeFFFFFeFFq......",
    ".....qFFFFFFFFFFFq......",
    "....qFFFFFFFFFFFFq......",
    "qqq.qFFFFffFFFFFFFqqq...",
    "qqqqqFFffffffFFFqqqqqq..",
    "qqqqQFffffffffFFFQqqqq..",
    "qqqQQFFFfffffFFFFQQqq...",
    "qQQQQFFFFFFFFFFFFQQQq...",
    "QQqqqqFFFFFFFFFFqqqqQ...",
    "...qqqqFFFFFFFFqqqq.....",
    ".....qqFFFFFFFFqq.......",
    ".......FF....FF.........",
    "......qFq....qFq........",
    "......TTT....TTT........",
]

# --- Design 4: Twilight Wyvern (purple, side flying) ---
DRAGON_4 = [
    "............MM..........",
    "............mMM.........",
    "............uUUUU.......",
    "..........uuUUeUUu......",
    "........iiUUUUUUUu......",
    "......iiiUUUUUUUUu......",
    ".....iiiiUUUUUUUu.......",
    "....iiiiiUUUUUUuu.......",
    "....iiIIiUUUUUUuu.......",
    "....iIIIIUUUUUUUu.......",
    ".....iIIIuUUUUUUu.......",
    "......iiIuUUUUUUu.......",
    "........uUUUUUUu........",
    ".......uUUUUUUu.........",
    "......uUUUUUu...........",
    ".....uUUUUu.............",
    "...uUUUUu...............",
    "..uuUu..................",
]

DESIGNS = [
    ("1. Forest Western",   DRAGON_1),
    ("2. Sky Serpent",      DRAGON_2),
    ("3. Crimson Spread",   DRAGON_3),
    ("4. Twilight Wyvern",  DRAGON_4),
]

SCALE = 7
GAP = 26
LABEL_H = 32
BG = (28, 30, 38, 255)

sw = 24 * SCALE
sh = 18 * SCALE
W = GAP + 2 * (sw + GAP)
H = GAP + 2 * (sh + LABEL_H + GAP)
canvas = Image.new("RGBA", (W, H), BG)
draw = ImageDraw.Draw(canvas)

try:
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 20)
except Exception:
    font = ImageFont.load_default()

for i, (label, sprite) in enumerate(DESIGNS):
    col, row = i % 2, i // 2
    x = GAP + col * (sw + GAP)
    y = GAP + row * (sh + LABEL_H + GAP)
    img = crab_image(sprite, scale=SCALE)
    canvas.paste(img, (x, y), img)
    draw.text((x, y + sh + 8), label, fill=(235, 235, 240, 255), font=font)

out = Path.home() / "Desktop" / "dragon-designs-v2.png"
canvas.save(str(out))
print(f"Saved {out}")
