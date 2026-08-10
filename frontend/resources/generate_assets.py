"""
Generates Capacitor icon + splash assets using a stylized 'F' (Facette).
Outputs:
  - resources/icon.png (1024x1024) — used by @capacitor/assets
  - resources/icon-foreground.png (1024x1024) — Android adaptive foreground (transparent)
  - resources/icon-background.png (1024x1024) — Android adaptive background (solid)
  - resources/splash.png (2732x2732) — splash artwork
  - resources/splash-dark.png (2732x2732) — dark splash variant
"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT = os.path.dirname(os.path.abspath(__file__))

# Brand palette
BG = (15, 15, 17)          # near-black
ACCENT = (212, 175, 55)    # gold (facette luxury)
WHITE = (255, 255, 255)


def find_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def draw_F(canvas_size, fg_color, bg_color=None, padding_ratio=0.18):
    """Draw a stylized 'F' centered on canvas."""
    img = Image.new("RGBA", (canvas_size, canvas_size), bg_color if bg_color else (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_path = find_font()
    # Leave padding around the F
    target_height = int(canvas_size * (1 - padding_ratio * 2))

    if font_path:
        # Iterate to find best font size
        size = target_height
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), "F", font=font)
        while (bbox[3] - bbox[1]) > target_height and size > 20:
            size -= 10
            font = ImageFont.truetype(font_path, size)
            bbox = draw.textbbox((0, 0), "F", font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (canvas_size - text_w) // 2 - bbox[0]
        y = (canvas_size - text_h) // 2 - bbox[1]
        draw.text((x, y), "F", font=font, fill=fg_color)
    else:
        # Manual geometric F
        pad = int(canvas_size * padding_ratio)
        stroke = int(canvas_size * 0.14)
        left = pad
        right = canvas_size - pad
        top = pad
        bottom = canvas_size - pad
        # vertical bar
        draw.rectangle([left, top, left + stroke, bottom], fill=fg_color)
        # top bar
        draw.rectangle([left, top, right, top + stroke], fill=fg_color)
        # mid bar (shorter)
        mid_y = top + (bottom - top) // 2 - stroke // 2
        mid_right = left + int((right - left) * 0.78)
        draw.rectangle([left, mid_y, mid_right, mid_y + stroke], fill=fg_color)
    return img


def make_icon():
    size = 1024
    img = Image.new("RGB", (size, size), BG)
    f = draw_F(size, ACCENT)
    img.paste(f, (0, 0), f)
    img.save(os.path.join(OUT, "icon.png"))


def make_icon_foreground():
    # Android adaptive icon foreground — must have ~33% safe-zone padding
    size = 1024
    f = draw_F(size, ACCENT, bg_color=None, padding_ratio=0.30)
    f.save(os.path.join(OUT, "icon-foreground.png"))


def make_icon_background():
    size = 1024
    img = Image.new("RGB", (size, size), BG)
    img.save(os.path.join(OUT, "icon-background.png"))


def make_splash(filename, bg, fg):
    size = 2732
    img = Image.new("RGB", (size, size), bg)
    f = draw_F(size, fg, bg_color=None, padding_ratio=0.38)
    img.paste(f, (0, 0), f)
    img.save(os.path.join(OUT, filename))


if __name__ == "__main__":
    make_icon()
    make_icon_foreground()
    make_icon_background()
    make_splash("splash.png", BG, ACCENT)
    make_splash("splash-dark.png", (0, 0, 0), ACCENT)
    print("Generated assets in", OUT)
    for f in ["icon.png", "icon-foreground.png", "icon-background.png", "splash.png", "splash-dark.png"]:
        p = os.path.join(OUT, f)
        print(f, os.path.getsize(p), "bytes")
