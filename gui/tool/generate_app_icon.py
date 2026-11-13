#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 macOS 应用图标（AppIcon.appiconset）。

设计：紫蓝渐变圆角方形 + 白色「文」字（与界面左上角标记一致），
不是 Flutter 默认图标。依赖 Pillow 与系统中文字体：
    python3 -m pip install pillow
    python3 gui/tool/generate_app_icon.py
输出直接写入 gui/macos/Runner/Assets.xcassets/AppIcon.appiconset/。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

OUT = Path(__file__).resolve().parents[1] / \
    "macos" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"

SS = 3
BASE = 1024
S = BASE * SS
N = 256                      # 低分辨率画渐变，再放大，避免色带

# ---------- 平滑对角渐变 ----------
small = Image.new("L", (N, N))
px = small.load()
for y in range(N):
    for x in range(N):
        t = (x + y) / (2 * (N - 1))
        px[x, y] = int(255 * (t ** 0.92))
grad_l = small.resize((S, S), Image.LANCZOS)

c1 = (66, 99, 236)     # #4263EC
c2 = (139, 84, 239)    # #8B54EF
bg = Image.new("RGB", (S, S))
gp = bg.load()
gl = grad_l.load()
for y in range(S):
    for x in range(S):
        t = gl[x, y] / 255
        gp[x, y] = (int(c1[0] + (c2[0] - c1[0]) * t),
                    int(c1[1] + (c2[1] - c1[1]) * t),
                    int(c1[2] + (c2[2] - c1[2]) * t))

# ---------- 左上柔光 + 右下压暗 ----------
hl = Image.new("L", (N, N))
dark_img = Image.new("L", (N, N))
hp, dp = hl.load(), dark_img.load()
for y in range(N):
    for x in range(N):
        dx, dy = x / N - 0.24, y / N - 0.16
        d = min(1.0, (dx * dx + dy * dy) ** 0.5 / 0.85)
        hp[x, y] = int(52 * (1 - d) ** 2)
        ex, ey = x / N - 0.85, y / N - 0.9
        d2 = min(1.0, (ex * ex + ey * ey) ** 0.5 / 0.9)
        dp[x, y] = int(46 * (1 - d2) ** 2)
bg = Image.composite(Image.new("RGB", (S, S), "#FFFFFF"), bg,
                     hl.resize((S, S), Image.LANCZOS))
bg = Image.composite(Image.new("RGB", (S, S), "#2B1B63"), bg,
                     dark_img.resize((S, S), Image.LANCZOS))

# ---------- 圆角方形 ----------
inset = int(68 * SS)
side = S - inset * 2
radius = int(side * 0.225)

def squircle_mask(size, inset, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [inset, inset, size - inset, size - inset], radius=radius, fill=255)
    return m

mask = squircle_mask(S, inset, radius)

# ---------- 阴影 ----------
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sh.paste((6, 8, 26, 255), (0, int(12 * SS)),
         mask.filter(ImageFilter.GaussianBlur(int(28 * SS))).point(lambda v: int(v * 0.45)))
icon = Image.alpha_composite(icon, sh)
icon.paste(bg, (0, 0), mask)

# ---------- 边缘细高光 ----------
d = ImageDraw.Draw(icon)
d.rounded_rectangle([inset + 2 * SS, inset + 2 * SS, S - inset - 2 * SS, S - inset - 2 * SS],
                    radius=radius - 2 * SS, outline=(255, 255, 255, 46), width=int(2.4 * SS))

# ---------- 文字 ----------
font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", int(566 * SS))
glyph = "文"
bbox = d.textbbox((0, 0), glyph, font=font)
tx = (S - (bbox[2] - bbox[0])) / 2 - bbox[0]
ty = (S - (bbox[3] - bbox[1])) / 2 - bbox[1]

ts = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(ts).text((tx, ty + 11 * SS), glyph, font=font, fill=(12, 10, 45, 130))
icon = Image.alpha_composite(icon, ts.filter(ImageFilter.GaussianBlur(int(10 * SS))))
ImageDraw.Draw(icon).text((tx, ty), glyph, font=font, fill=(255, 255, 255, 246))

# ---------- 输出 ----------
master = icon.resize((BASE, BASE), Image.LANCZOS)
master.save(OUT / "app_icon_1024.png")
for size in (16, 32, 64, 128, 256, 512):
    master.resize((size, size), Image.LANCZOS).save(OUT / f"app_icon_{size}.png")
print(f"已生成 7 个尺寸 → {OUT}")
