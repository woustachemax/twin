"""Generates assets/dmg/background.png, the .dmg installer window background.

Three things matter here that are easy to get wrong with create-dmg:

1. Retina crispness. create-dmg does no scaling of its own - it just copies
   whatever file you give it into the volume's .background folder and points
   Finder at it (see its --background handling). Finder renders that image
   using its embedded DPI: an image with no DPI metadata (PIL's default) is
   treated as 1 image pixel = 1 point, so a plain 2x-sized image just gets
   cropped to the window's top-left corner instead of scaling down. The fix
   is to render at 2x pixel dimensions AND tag the PNG at 144 DPI (2x of the
   standard 72), which tells Finder to display it scaled to WINDOW_W x
   WINDOW_H points, at full retina density. Text is drawn directly at that
   2x pixel size (not drawn small and stretched up), so it stays properly
   anti-aliased instead of looking blurry.

2. Resizing past the image edge. Neither create-dmg nor Finder's AppleScript
   dictionary expose a way to lock a container window's resize handle - there
   is no such property to set. Finder's icon view options do have a separate
   `background color`, but it's not a fallback that shows through around a
   `background picture`: the two are mutually exclusive display modes
   (confirmed empirically - setting a picture after a color silently drops
   the color back to white, and Finder does not composite them). So instead
   of a real lock or a working color fallback, this renders the background
   onto a canvas much larger than the default window (PADDED_W x PADDED_H)
   with the designed content anchored at the same top-left-relative
   position. The default WINDOW_W x WINDOW_H view looks identical to a
   tightly-cropped image; dragging the window bigger reveals more solid
   brand-dark canvas instead of white, up to PADDED_W x PADDED_H. This is a
   mitigation for realistic manual resizing, not a real fix - drag the
   window bigger than PADDED_W x PADDED_H and white reappears.

3. Icon label legibility. Finder can render icon-view labels in black text
   (the classic default; some macOS/appearance combinations lighten it, but
   that isn't reliable enough to design around), which is illegible on a
   dark background image and isn't controllable from the background image
   or create-dmg. Rather than fight label color, this draws a light, subtle
   rounded pad directly behind where Finder actually places each label, so
   black text has contrast regardless. The pad position/size below was
   measured empirically from a real rendered window at ICON_SIZE=96 and
   TEXT_SIZE=16 (both hardcoded in scripts/build_dmg.sh) - if either changes
   there, re-measure LABEL_CENTER_Y and the per-label pad widths.

Icon/app-link slots must line up with the --icon / --app-drop-link
coordinates passed to create-dmg in scripts/build_dmg.sh.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "background.png")
FONT_DIR = os.path.join(HERE, "..", "fonts")
DISPLAY_FONT = os.path.join(FONT_DIR, "BricolageGrotesque-ExtraBold.ttf")
MONO_FONT = os.path.join(FONT_DIR, "JetBrainsMono-Regular.ttf")
SYSTEM_FONT = "/System/Library/Fonts/SFNS.ttf"  # matches Finder's own label font

PIXEL_SCALE = 2  # retina: render at 2x, tag the PNG at 144 DPI (below)
WINDOW_W, WINDOW_H = 540, 380
PADDED_W, PADDED_H = 1600, 1200  # see point 2 above - covers realistic manual resizing
ICON_X, ICON_Y = 140, 190
APP_X, APP_Y = 400, 190

# Empirically measured (see point 3 above): label text vertical center sits
# ~65pt below the icon's own center at ICON_SIZE=96/TEXT_SIZE=16. Measured by
# rendering a first pass, screenshotting the real mounted window, and
# comparing pixel positions of the rendered pad vs Finder's actual label text
# - the first attempt (a formula-based guess) was off by 32pt.
LABEL_CENTER_Y = ICON_Y + 65
LABEL_PAD_HEIGHT = 24
LABELS = {ICON_X: "Twin", APP_X: "Applications"}

# from buddy.py's LANDING dict / landing/index.html's --bg, --text, etc.
BG = "#0E0D12"
TEXT = "#F3EEFC"
MUTED = "#A39DB3"
LIME = "#C6FF4A"
PINK = "#FF7AB8"
LABEL_PAD = "#EDE9F5"


def squiggle(draw, x0, x1, y, color, width, amplitude=2.2, wavelength=15):
    points = []
    x = x0
    while x <= x1:
        t = (x - x0) / wavelength * 2 * math.pi
        points.append((x, y + amplitude * math.sin(t)))
        x += 1
    draw.line(points, fill=color, width=width, joint="curve")


def curved_arrow(draw, x0, x1, y, color, width, bow=16):
    """A single thin, slightly-curved arrow from (x0,y) to (x1,y)."""
    points = []
    steps = int(x1 - x0)
    for i in range(steps + 1):
        t = i / steps
        x = x0 + t * (x1 - x0)
        yy = y - bow * math.sin(t * math.pi)
        points.append((x, yy))
    draw.line(points, fill=color, width=width, joint="curve")

    tx, ty = points[-1]
    px, py = points[-3]
    angle = math.atan2(ty - py, tx - px)
    head_len = width * 6.5
    spread = math.radians(28)
    for sign in (-1, 1):
        a = angle + math.pi - sign * spread
        draw.line(
            [(tx, ty), (tx + head_len * math.cos(a), ty + head_len * math.sin(a))],
            fill=color, width=width, joint="curve",
        )


def px(v):
    return v * PIXEL_SCALE


def main():
    w, h = px(PADDED_W), px(PADDED_H)
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)

    word_font = ImageFont.truetype(DISPLAY_FONT, px(21))
    caption_font = ImageFont.truetype(MONO_FONT, px(11))
    label_font = ImageFont.truetype(SYSTEM_FONT, px(16))

    word_x, word_y = px(32), px(26)
    draw.text((word_x, word_y), "twin", font=word_font, fill=TEXT)
    word_bbox = draw.textbbox((word_x, word_y), "twin", font=word_font)
    squiggle(
        draw,
        word_bbox[0], word_bbox[2],
        word_bbox[3] + px(5),
        PINK, max(2, px(1) + 1),
        amplitude=px(2.2), wavelength=px(15),
    )

    curved_arrow(
        draw,
        px(ICON_X + 58), px(APP_X - 58), px(ICON_Y),
        MUTED, max(1, int(px(0.9))), bow=px(15),
    )

    for icon_x, label in LABELS.items():
        tw = draw.textlength(label, font=label_font)
        pad_w = tw + px(20)
        pad_h = px(LABEL_PAD_HEIGHT)
        cx, cy = px(icon_x), px(LABEL_CENTER_Y)
        draw.rounded_rectangle(
            [cx - pad_w / 2, cy - pad_h / 2, cx + pad_w / 2, cy + pad_h / 2],
            radius=pad_h / 2.4, fill=LABEL_PAD,
        )

    # Finder's window "bounds" include the title bar, so the last ~35-40pt of
    # WINDOW_H render behind/below the visible content area (confirmed by
    # screenshot - see scripts/build_dmg.sh's window-size comment). Keep
    # anything meaningful above WINDOW_H - 46 to stay clear of that band.
    caption = "drag to install"
    cw = draw.textlength(caption, font=caption_font)
    draw.text(((px(WINDOW_W) - cw) / 2, px(WINDOW_H - 46)), caption, font=caption_font, fill=MUTED)

    img.save(OUT_PATH, dpi=(72 * PIXEL_SCALE, 72 * PIXEL_SCALE))
    print(f"wrote {OUT_PATH}: {w}x{h}px @ {72 * PIXEL_SCALE} dpi "
          f"(default view {WINDOW_W}x{WINDOW_H}pt, padded to {PADDED_W}x{PADDED_H}pt)")


if __name__ == "__main__":
    main()
