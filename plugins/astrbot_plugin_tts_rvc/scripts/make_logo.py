"""生成项目 logo（512x512 PNG）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logo.png"

SIZE = 512
PINK = (255, 138, 174)
BLUE = (120, 170, 255)
INK = (46, 52, 72)


def lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def gradient() -> Image.Image:
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = image.load()
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x + y) / (2 * SIZE - 2)
            pixels[x, y] = (
                lerp(255, 214, t),
                lerp(233, 236, t),
                lerp(243, 255, t),
            )
    return image


def main() -> None:
    base = gradient().convert("RGBA")

    # 圆角遮罩
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=112, fill=255)
    base.putalpha(mask)

    draw = ImageDraw.Draw(base)

    # 柔光圆
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((96, 60, 416, 380), fill=(255, 255, 255, 120))
    base.alpha_composite(glow)

    # 语音气泡
    bubble = (104, 92, 408, 336)
    draw.rounded_rectangle(bubble, radius=72, fill=(255, 255, 255, 235))
    draw.polygon([(196, 330), (196, 392), (264, 330)], fill=(255, 255, 255, 235))

    # 声波柱
    bars = [
        (166, 176, 186, 252),
        (200, 148, 222, 280),
        (236, 128, 258, 300),
        (272, 156, 294, 272),
        (308, 188, 330, 240),
    ]
    for x0, y0, x1, y1 in bars:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=None)
        width = y1 - y0
        for i in range(width):
            t = i / max(width - 1, 1)
            color = (
                lerp(PINK[0], BLUE[0], t),
                lerp(PINK[1], BLUE[1], t),
                lerp(PINK[2], BLUE[2], t),
                255,
            )
            draw.line((x0, y0 + i, x1, y0 + i), fill=color)

    # 底部文字
    font = None
    for candidate in (
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 46)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "TTS · RVC"
    box = draw.textbbox((0, 0), text, font=font)
    tw = box[2] - box[0]
    draw.text(((SIZE - tw) / 2 - box[0], 392), text, font=font, fill=INK)

    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26) if Path(r"C:\Windows\Fonts\msyh.ttc").exists() else font
    sub = "把你的声音接进来"
    box2 = draw.textbbox((0, 0), sub, font=small)
    draw.text(((SIZE - (box2[2] - box2[0])) / 2 - box2[0], 452), sub, font=small, fill=(110, 118, 145))

    base.save(OUT)
    print("saved", OUT, base.size)


if __name__ == "__main__":
    main()
