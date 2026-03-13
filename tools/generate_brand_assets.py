from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = ROOT / "static" / "brand"
BRAND_DIR.mkdir(parents=True, exist_ok=True)

SIZE = 512
BACKGROUND = (0, 0, 0, 0)
PLANE_FILL = "#f4f7fb"
PLANE_EDGE = "#cfd8e3"
BLUE = "#78a2e3"
GREEN = "#8fce63"
YELLOW = "#e8d57b"


def trail(draw: ImageDraw.ImageDraw, points, color, width):
    draw.line(points, fill=color, width=width, joint="curve")
    radius = width // 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def draw_logo(draw: ImageDraw.ImageDraw):
    trail(draw, [(116, 358), (196, 332), (276, 292), (342, 232)], BLUE, 24)
    trail(draw, [(132, 392), (216, 360), (292, 318), (356, 254)], GREEN, 28)
    trail(draw, [(156, 426), (246, 390), (324, 342), (384, 280)], YELLOW, 30)

    plane = [(188, 286), (424, 168), (290, 332), (244, 374), (228, 326)]
    fold = [(244, 374), (250, 316), (290, 332)]
    wing = [(188, 286), (292, 320), (424, 168)]

    draw.polygon(wing, fill="#eef2f7")
    draw.polygon(plane, fill=PLANE_FILL, outline=PLANE_EDGE)
    draw.polygon(fold, fill="#dde5ef", outline=PLANE_EDGE)
    draw.line([(188, 286), (246, 328), (424, 168)], fill=PLANE_EDGE, width=3)


def build_png():
    image = Image.new("RGBA", (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw_logo(draw)
    image.save(BRAND_DIR / "Draft-mark.png")
    image.save(BRAND_DIR / "draftai-mark.png")
    return image


def build_ico(image: Image.Image):
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    image.save(BRAND_DIR / "Draft-mark.ico", sizes=sizes)
    image.save(BRAND_DIR / "draftai-mark.ico", sizes=sizes)


def build_svg():
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Draft logo">
  <path d="M116 358 C176 342 228 310 342 232" fill="none" stroke="{BLUE}" stroke-width="24" stroke-linecap="round"/>
  <path d="M132 392 C206 364 266 328 356 254" fill="none" stroke="{GREEN}" stroke-width="28" stroke-linecap="round"/>
  <path d="M156 426 C236 392 302 356 384 280" fill="none" stroke="{YELLOW}" stroke-width="30" stroke-linecap="round"/>
  <polygon points="188,286 292,320 424,168" fill="#eef2f7"/>
  <polygon points="188,286 424,168 290,332 244,374 228,326" fill="{PLANE_FILL}" stroke="{PLANE_EDGE}" stroke-width="3" stroke-linejoin="round"/>
  <polygon points="244,374 250,316 290,332" fill="#dde5ef" stroke="{PLANE_EDGE}" stroke-width="3" stroke-linejoin="round"/>
  <polyline points="188,286 246,328 424,168" fill="none" stroke="{PLANE_EDGE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''
    (BRAND_DIR / "Draft-mark.svg").write_text(svg, encoding="utf-8")
    (BRAND_DIR / "draftai-mark.svg").write_text(svg, encoding="utf-8")


def main():
    image = build_png()
    build_ico(image)
    build_svg()
    print(f"Brand assets generated in {BRAND_DIR}")


if __name__ == "__main__":
    main()
