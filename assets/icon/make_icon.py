import math
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ICNS_PATH = os.path.join(HERE, "Twin.icns")
PREVIEW_PATH = os.path.join(HERE, "Twin-1024.png")
PERSONA_DIR = os.path.join(HERE, "personas")
FONT_PATH = os.path.join(HERE, "..", "fonts", "BricolageGrotesque-ExtraBold.ttf")

TILE = (20, 18, 27)
TILE_EDGE = (44, 41, 56)
LIME = (198, 255, 74)
LIME_SHADE = (158, 214, 36)
FACE = (17, 17, 17)
CHEEK = (255, 122, 184)
GROUND = (12, 11, 16)

PERSONA_ICONS = {
    "gengar": {"background": "#17111F", "accent": "#A77BFF", "text": "#EEE8F7", "eyes": "#FF4F6E", "avatar": "ghost",
               "expression": "sly"},
    "ember": {"background": "#1E120D", "accent": "#FF7A3D", "text": "#FFEDE4", "eyes": "#FFD166", "avatar": "ghost",
              "expression": "excited"},
    "calm": {"background": "#141828", "accent": "#A5B4FF", "text": "#E7EBFA", "eyes": "#141828", "avatar": "ghost"},
    "plain": {"background": "#1E1E20", "accent": "#8E8E93", "text": "#F2F2F7", "eyes": "#1E1E20", "avatar": "monogram",
              "letter": "A"},
}

PERSONA_ICON_PIXELS = 512
SUPERSAMPLE = 4
ICONSET = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]


def squircle(size, margin, exponent=5.0, steps=720):
    radius = (size - 2 * margin) / 2
    center = size / 2
    points = []
    for i in range(steps):
        angle = 2 * math.pi * i / steps
        c, s = math.cos(angle), math.sin(angle)
        x = center + radius * math.copysign(abs(c) ** (2 / exponent), c)
        y = center + radius * math.copysign(abs(s) ** (2 / exponent), s)
        points.append((x, y))
    return points


def ellipse_mask(size, box):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


def fill(image, mask, color):
    image.paste(Image.new("RGBA", image.size, color + (255,)), (0, 0), mask)


def render(pixels):
    size = pixels * SUPERSAMPLE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    detail = "full" if pixels >= 64 else ("medium" if pixels >= 48 else "tiny")

    margin = size * (100 / 1024) if pixels >= 64 else size * (60 / 1024)
    draw.polygon(squircle(size, margin), fill=TILE_EDGE + (255,))
    draw.polygon(squircle(size, margin + size * 0.006), fill=TILE + (255,))

    scale = {"full": 0.56, "medium": 0.64, "tiny": 0.74}[detail]
    body_w = size * scale
    body_h = body_w * (108 / 118)
    cx, cy = size / 2, size / 2 + size * 0.02
    left, top = cx - body_w / 2, cy - body_h / 2
    body_box = (left, top, left + body_w, top + body_h)

    if detail != "tiny":
        ground_w = body_w * 0.78
        ground_box = (cx - ground_w / 2, top + body_h * 0.93, cx + ground_w / 2, top + body_h * 1.05)
        fill(image, ellipse_mask(size, ground_box), GROUND)

    body = ellipse_mask(size, body_box)
    fill(image, body, LIME_SHADE)
    shift_x, shift_y = body_w * (10 / 118), body_h * (12 / 108)
    highlight = ellipse_mask(size, (left - shift_x, top - shift_y, left + body_w - shift_x, top + body_h - shift_y))
    fill(image, ImageChops.multiply(body, highlight), LIME)

    def at(fx, fy):
        return left + body_w * fx, top + body_h * fy

    eye_w = body_w * {"full": 12 / 118, "medium": 15 / 118, "tiny": 19 / 118}[detail]
    eye_h = body_h * {"full": 20 / 108, "medium": 22 / 108, "tiny": 24 / 108}[detail]
    eye_y = 0.43 if detail != "tiny" else 0.47
    for fx in (0.345, 0.600):
        ex, ey = at(fx, eye_y)
        draw.rounded_rectangle(
            (ex - eye_w / 2, ey - eye_h / 2, ex + eye_w / 2, ey + eye_h / 2), radius=eye_w / 2, fill=FACE + (255,),
        )

    if detail == "full":
        cheek_w, cheek_h = body_w * (16 / 118), body_h * (8 / 108)
        for fx in (0.235, 0.725):
            chx, chy = at(fx, 0.61)
            cheek = ellipse_mask(size, (chx - cheek_w / 2, chy - cheek_h / 2, chx + cheek_w / 2, chy + cheek_h / 2))
            fill(image, ImageChops.multiply(cheek, body), tuple(round(c * 0.7 + l * 0.3) for c, l in zip(CHEEK, LIME)))

    if detail != "tiny":
        mouth_w = body_w * (22 / 118)
        mx, my = at(0.472, 0.575)
        stroke = max(1, round(body_w * (3.4 / 118)))
        draw.arc((mx - mouth_w / 2, my - mouth_w / 2, mx + mouth_w / 2, my + mouth_w / 2), start=20, end=160,
                 fill=FACE + (255,), width=stroke)

    return image.resize((pixels, pixels), Image.LANCZOS)


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def blend(a, b, amount):
    return tuple(round(x * (1 - amount) + y * amount) for x, y in zip(rgb(a), rgb(b)))


def bezier_points(p0, p1, p2, p3, steps=24):
    points = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0] + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0]
        y = mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1] + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1]
        points.append((x, y))
    return points


def draw_face(image, draw, left, top, width, height, eye_color, pixels, expression):
    """Draws the ghost's eyes and mouth. "content" is the original symmetric
    round-eyes-plus-gentle-smile look every persona used to share regardless of its written
    personality. "sly" and "excited" give gengar and ember their own actual expression
    instead of only differing by fill color, built from the same primitive shapes (rounded
    rectangles, a bezier stroke) so they still come out of this one generator rather than a
    one-off hand-drawn asset."""
    if expression == "sly":
        # Asymmetric on purpose: a raised, narrowed "scheming" eye on one side and a
        # half-closed wink on the other reads as mischief in a way two identical eyes can't.
        eye_w, eye_h = width * 0.115, height * 0.20
        eye_specs = [(0.335, 1.0, 8, 0.40), (0.665, 0.48, -10, 0.34)]
        for fx, h_scale, angle, y_frac in eye_specs:
            ew, eh = eye_w, eye_h * h_scale
            pad = max(ew, eh)
            layer = Image.new("RGBA", (int(ew + pad), int(eh + pad)), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rounded_rectangle(
                (pad / 2, pad / 2, pad / 2 + ew, pad / 2 + eh), radius=ew / 2, fill=eye_color,
            )
            layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
            ex, ey = left + width * fx, top + height * y_frac
            box = (round(ex - layer.width / 2), round(ey - layer.height / 2))
            image.paste(layer, box, layer)
        if pixels >= 48:
            mouth = width * 0.24
            mx, my = left + width * 0.47, top + height * 0.575
            stroke = max(1, round(width * 0.032))
            # One corner low and flat, the other pulled sharply up: a smirk, not a smile.
            points = bezier_points(
                (mx - mouth / 2, my + mouth * 0.06),
                (mx - mouth * 0.08, my + mouth * 0.22),
                (mx + mouth * 0.18, my - mouth * 0.05),
                (mx + mouth / 2, my - mouth * 0.42),
            )
            draw.line(points, fill=eye_color, width=stroke, joint="curve")
            r = stroke / 2
            for px, py in (points[0], points[-1]):
                draw.ellipse((px - r, py - r, px + r, py + r), fill=eye_color)
    elif expression == "excited":
        # Bigger, rounder eyes and a wide open grin (a filled shape, not just an outline)
        # for a persona that's supposed to read as bright and enthusiastic, not just "happy."
        eye_w, eye_h = width * 0.125, height * 0.225
        for fx in (0.33, 0.67):
            ex, ey = left + width * fx, top + height * 0.415
            draw.rounded_rectangle((ex - eye_w / 2, ey - eye_h / 2, ex + eye_w / 2, ey + eye_h / 2),
                                   radius=eye_w / 2, fill=eye_color)
        if pixels >= 48:
            # A wide open oval, not a smile outline: reads as an open cheer/laugh rather
            # than a flat "surprised" slot the way a straight-edged rounded rect did.
            mouth_w, mouth_h = width * 0.30, height * 0.20
            mx, my = left + width / 2, top + height * 0.59
            draw.ellipse((mx - mouth_w / 2, my - mouth_h / 2, mx + mouth_w / 2, my + mouth_h / 2), fill=eye_color)
    else:
        eye_w, eye_h = width * 0.11, height * 0.19
        for fx in (0.33, 0.67):
            ex, ey = left + width * fx, top + height * 0.42
            draw.rounded_rectangle((ex - eye_w / 2, ey - eye_h / 2, ex + eye_w / 2, ey + eye_h / 2),
                                   radius=eye_w / 2, fill=eye_color)
        if pixels >= 48:
            mouth = width * 0.2
            mx, my = left + width / 2, top + height * 0.56
            draw.arc((mx - mouth / 2, my - mouth / 2, mx + mouth / 2, my + mouth / 2), start=20, end=160,
                     fill=eye_color, width=max(1, round(width * 0.03)))


def ghost_polygon(left, top, width, height, waves=3, steps=24):
    radius = width / 2
    points = []
    for i in range(steps + 1):
        angle = math.pi + math.pi * i / steps
        points.append((left + radius + radius * math.cos(angle), top + radius + radius * math.sin(angle)))
    hem = top + height
    base = hem - height * 0.12
    points.append((left + width, base))
    segments = waves * 2
    for i in range(segments + 1):
        x = left + width - width * i / segments
        points.append((x, hem if i % 2 == 0 else base))
    return points


def render_persona(pixels, spec):
    size = pixels * SUPERSAMPLE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size * (100 / 1024) if pixels >= 64 else size * (60 / 1024)
    tile = rgb(spec["background"])
    accent = rgb(spec["accent"])
    draw.polygon(squircle(size, margin), fill=blend(spec["background"], spec["accent"], 0.25) + (255,))
    draw.polygon(squircle(size, margin + size * 0.006), fill=tile + (255,))

    cx, cy = size / 2, size / 2
    if spec["avatar"] == "ghost":
        width = size * 0.5
        height = size * 0.5
        left, top = cx - width / 2, cy - height / 2 - size * 0.01
        shade = blend(spec["accent"], "#000000", 0.22)
        shadow_w = width * 0.8
        draw.ellipse((cx - shadow_w / 2, top + height * 1.02, cx + shadow_w / 2, top + height * 1.1),
                     fill=blend(spec["background"], "#000000", 0.4) + (255,))
        draw.polygon(ghost_polygon(left, top, width, height), fill=shade + (255,))
        body = Image.new("L", (size, size), 0)
        ImageDraw.Draw(body).polygon(ghost_polygon(left - width * 0.03, top - height * 0.03, width, height), fill=255)
        clip = Image.new("L", (size, size), 0)
        ImageDraw.Draw(clip).polygon(ghost_polygon(left, top, width, height), fill=255)
        fill(image, ImageChops.multiply(body, clip), accent)
        eye = rgb(spec["eyes"]) + (255,)
        draw_face(image, draw, left, top, width, height, eye, pixels, spec.get("expression"))
    else:
        diameter = size * 0.5
        draw.ellipse((cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2),
                     fill=blend(spec["background"], spec["accent"], 0.18) + (255,),
                     outline=accent + (255,), width=max(1, round(size * 0.008)))
        font = ImageFont.truetype(FONT_PATH, round(diameter * 0.62))
        draw.text((cx, cy), spec["letter"], font=font, fill=rgb(spec["text"]) + (255,), anchor="mm")

    return image.resize((pixels, pixels), Image.LANCZOS)


def write_persona_icons():
    os.makedirs(PERSONA_DIR, exist_ok=True)
    render(PERSONA_ICON_PIXELS).save(os.path.join(PERSONA_DIR, "twin.png"))
    for key, spec in PERSONA_ICONS.items():
        render_persona(PERSONA_ICON_PIXELS, spec).save(os.path.join(PERSONA_DIR, f"{key}.png"))
    return sorted(os.listdir(PERSONA_DIR))


def main():
    if shutil.which("iconutil") is None:
        sys.exit("iconutil not found: building .icns files needs macOS.")
    workdir = tempfile.mkdtemp(prefix="twin-icon-")
    iconset = os.path.join(workdir, "Twin.iconset")
    os.makedirs(iconset)
    try:
        cache = {}
        for name, pixels in ICONSET:
            if pixels not in cache:
                cache[pixels] = render(pixels)
            cache[pixels].save(os.path.join(iconset, name))
        cache[1024].save(PREVIEW_PATH)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", ICNS_PATH], check=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"wrote {ICNS_PATH} and {PREVIEW_PATH}")
    print("wrote persona icons:", ", ".join(write_persona_icons()))


if __name__ == "__main__":
    main()
