"""Build language-specific marketing visuals from real product evidence.

Run from the repository root with Pillow installed:

    python scripts/generate_marketing_assets.py

The script never invents product UI. It derives both outputs from screenshots
captured from the local OpenAlphaStack Dashboard.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
SCREENSHOTS = [
    (ASSETS / "dashboard-search-results.png", "按代码、中文名称与拼音搜索 A 股"),
    (ASSETS / "dashboard-stock-search.png", "查看 K 线、计划与模拟盘状态"),
    (ASSETS / "dashboard-workflow.png", "审计 Research → Execution → Evaluation"),
]

GOLD = "#f5b51b"
TEAL = "#28d7c5"
INK = "#071019"
PANEL = "#0d1724"
MUTED = "#9eacc0"
WHITE = "#f4f7fb"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def _rounded_screenshot(
    image: Image.Image, size: tuple[int, int], radius: int = 18
) -> Image.Image:
    shot = _cover(image.convert("RGB"), size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    result = Image.new("RGBA", size, (0, 0, 0, 0))
    result.paste(shot, mask=mask)
    return result


def build_demo_gif() -> Path:
    width, height = 960, 600
    stable_frames: list[Image.Image] = []

    for screenshot_path, caption in SCREENSHOTS:
        source = Image.open(screenshot_path).convert("RGB")
        frame = _cover(source, (width, height))
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle((26, 25, 650, 88), radius=14, fill=(4, 12, 20, 225), outline=GOLD, width=2)
        draw.text((49, 43), caption, font=_font(24, bold=True), fill=WHITE)
        draw.rounded_rectangle((800, 31, 928, 80), radius=24, fill=(7, 16, 25, 230), outline=TEAL, width=2)
        draw.text((832, 45), "实机演示", font=_font(18, bold=True), fill=TEAL)
        stable_frames.append(Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB"))

    frames: list[Image.Image] = []
    durations: list[int] = []
    transition_steps = 5
    for index, frame in enumerate(stable_frames):
        frames.append(frame)
        durations.append(1700)
        next_frame = stable_frames[(index + 1) % len(stable_frames)]
        for step in range(1, transition_steps + 1):
            frames.append(Image.blend(frame, next_frame, step / (transition_steps + 1)))
            durations.append(90)

    quantized = [item.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for item in frames]
    output = ASSETS / "openalphastack-demo.gif"
    quantized[0].save(
        output,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return output


def _social_canvas() -> Image.Image:
    canvas = Image.new("RGB", (1280, 640), INK)
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-210, -260, 550, 500), fill=(245, 181, 27, 48))
    glow_draw.ellipse((780, 150, 1510, 830), fill=(40, 215, 197, 42))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    return Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")


def build_social_preview_zh() -> Path:
    canvas = _social_canvas()
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((64, 58, 251, 96), radius=19, fill=PANEL, outline=GOLD, width=2)
    draw.text((107, 66), "开源项目", font=_font(18, bold=True), fill=GOLD)
    draw.text((62, 128), "OpenAlphaStack", font=_font(59, bold=True), fill=WHITE)
    draw.text((65, 211), "Codex + MCP + Skills", font=_font(34, bold=True), fill=TEAL)
    draw.text((65, 260), "可审计的 A 股研究系统", font=_font(30, bold=True), fill=WHITE)

    body = [
        "智能体负责研究，代码负责确定性执行",
        "本地优先 · 公开透明 · 仅限模拟交易",
    ]
    for idx, line in enumerate(body):
        y = 335 + idx * 45
        draw.ellipse((67, y + 8, 79, y + 20), fill=GOLD if idx == 0 else TEAL)
        draw.text((95, y), line, font=_font(23), fill=MUTED)

    stages = [
        ("研究", 65, GOLD),
        ("执行", 236, TEAL),
        ("评估", 423, GOLD),
    ]
    for label, x, color in stages:
        draw.rounded_rectangle((x, 489, x + 145, 538), radius=12, fill=PANEL, outline=color, width=2)
        label_box = draw.textbbox((0, 0), label, font=_font(20, bold=True))
        label_width = label_box[2] - label_box[0]
        draw.text((x + (145 - label_width) / 2, 501), label, font=_font(20, bold=True), fill=WHITE)
    draw.line((210, 514, 232, 514), fill=MUTED, width=2)
    draw.line((381, 514, 419, 514), fill=MUTED, width=2)
    draw.text((65, 573), "github.com/44-99/OpenAlphaStack", font=_font(21, bold=True), fill=MUTED)

    # The Chinese preview uses the real Chinese-first product UI.
    search = Image.open(SCREENSHOTS[1][0]).convert("RGB")
    overview = Image.open(SCREENSHOTS[0][0]).convert("RGB")
    search = ImageEnhance.Brightness(search).enhance(1.05)
    overview = ImageEnhance.Brightness(overview).enhance(1.05)

    back = _rounded_screenshot(overview, (510, 319), radius=19)
    front = _rounded_screenshot(search, (545, 341), radius=19)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((703, 105, 1233, 444), radius=23, fill=(0, 0, 0, 150))
    shadow_draw.rounded_rectangle((660, 218, 1225, 579), radius=23, fill=(0, 0, 0, 175))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    composed = Image.alpha_composite(canvas.convert("RGBA"), shadow)
    composed.alpha_composite(back, (713, 95))
    composed.alpha_composite(front, (650, 208))

    border = ImageDraw.Draw(composed)
    border.rounded_rectangle((712, 94, 1224, 415), radius=20, outline=(245, 181, 27, 190), width=2)
    border.rounded_rectangle((649, 207, 1196, 550), radius=20, outline=(40, 215, 197, 210), width=2)

    output = ASSETS / "openalphastack-social-preview.png"
    composed.convert("RGB").save(output, optimize=True)
    return output


def build_social_preview_en() -> Path:
    canvas = _social_canvas()
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((64, 58, 246, 96), radius=19, fill=PANEL, outline=GOLD, width=2)
    draw.text((86, 66), "OPEN SOURCE", font=_font(18, bold=True), fill=GOLD)
    draw.text((62, 128), "OpenAlphaStack", font=_font(59, bold=True), fill=WHITE)
    draw.text((65, 211), "Auditable A-share research", font=_font(31, bold=True), fill=TEAL)
    draw.text((65, 262), "for Codex", font=_font(31, bold=True), fill=WHITE)

    body = [
        "Agent-led research. Code-owned execution.",
        "Local-first · Transparent · Paper only",
    ]
    for idx, line in enumerate(body):
        y = 349 + idx * 45
        draw.ellipse((67, y + 8, 79, y + 20), fill=GOLD if idx == 0 else TEAL)
        draw.text((95, y), line, font=_font(22), fill=MUTED)

    draw.text((65, 573), "github.com/44-99/OpenAlphaStack", font=_font(21, bold=True), fill=MUTED)

    cards = [
        ("01", "Codex tasks", "Intent and research", GOLD),
        ("02", "Domain Skills", "Reusable methods", GOLD),
        ("03", "MCP tools", "Typed capabilities", TEAL),
        ("04", "Python + SQLite", "Rules and evidence", TEAL),
    ]
    x, y = 690, 88
    for index, title, subtitle, color in cards:
        draw.rounded_rectangle((x, y, 1216, y + 106), radius=18, fill=PANEL, outline=color, width=2)
        draw.rounded_rectangle((x + 18, y + 20, x + 74, y + 76), radius=12, fill=INK, outline=color, width=1)
        draw.text((x + 31, y + 36), index, font=_font(17, bold=True), fill=color)
        draw.text((x + 96, y + 21), title, font=_font(25, bold=True), fill=WHITE)
        draw.text((x + 96, y + 59), subtitle, font=_font(18), fill=MUTED)
        if y < 440:
            draw.line((x + 263, y + 106, x + 263, y + 124), fill=MUTED, width=2)
        y += 124

    output = ASSETS / "openalphastack-social-preview-en.png"
    canvas.save(output, optimize=True)
    return output


if __name__ == "__main__":
    for generated in (build_demo_gif(), build_social_preview_zh(), build_social_preview_en()):
        print(f"generated {generated.relative_to(ROOT)} ({generated.stat().st_size:,} bytes)")
