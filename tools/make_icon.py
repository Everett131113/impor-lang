# -*- coding: utf-8 -*-
"""生成 Impor (I++) 应用图标（确定性矢量几何，不依赖生图模型）。

输出：
  impor/assets/icons/impor.svg       矢量源（设计稿/文档用）
  impor/assets/icons/impor-1024.png  母版
  impor/assets/icons/impor-512/256/128/64/48/32/16.png
  impor/assets/icons/impor.ico       Windows 多尺寸图标
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "impor", "assets", "icons")

S = 4                  # 超采样倍数
BASE = 1024
W = BASE * S

BG_TOP = (11, 14, 20)       # #0B0E14 与编辑器主题一致
BG_BOTTOM = (19, 26, 40)    # #131A28
ACCENT = (122, 162, 247)    # #7AA2F7
ACCENT_2 = (168, 196, 255)  # #A8C4FF
WHITE = (244, 247, 255)     # #F4F7FF
RADIUS = 224


def sc(v):
    return int(round(v * S))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    grad = Image.new("RGB", (1, size), top)
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return grad.resize((size, size), Image.BILINEAR)


def plus(d, cx, cy, arm, thick, fill):
    half = arm / 2.0
    ht = thick / 2.0
    d.rectangle([sc(cx - half), sc(cy - ht), sc(cx + half), sc(cy + ht)], fill=fill)
    d.rectangle([sc(cx - ht), sc(cy - half), sc(cx + ht), sc(cy + half)], fill=fill)


def build_master():
    bg = vertical_gradient(W, BG_TOP, BG_BOTTOM).convert("RGBA")
    bg.putalpha(rounded_mask(W, sc(RADIUS)))

    # 品牌蓝的柔和内光，避免纯色底显得平
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([sc(120), sc(120), sc(900), sc(900)], fill=(ACCENT[0], ACCENT[1], ACCENT[2], 18))
    bg = Image.alpha_composite(bg, glow)

    d = ImageDraw.Draw(bg)

    # 细描边（与编辑器强调色同源）
    d.rounded_rectangle(
        [sc(5), sc(5), sc(BASE - 5), sc(BASE - 5)],
        radius=sc(RADIUS - 4), outline=(ACCENT[0], ACCENT[1], ACCENT[2], 105), width=sc(2),
    )

    # 衬线体 I：粗竖干 + 上下横托，远距离仍是一个稳定的“I”
    stem_l, stem_r = 265, 365
    top_y, bot_y = 250, 774
    bar_l, bar_r = 225, 405
    bar_h = 58
    d.rounded_rectangle([sc(stem_l), sc(top_y), sc(stem_r), sc(bot_y)], radius=sc(10), fill=WHITE)
    d.rounded_rectangle([sc(bar_l), sc(top_y), sc(bar_r), sc(top_y + bar_h)], radius=sc(12), fill=WHITE)
    d.rounded_rectangle([sc(bar_l), sc(bot_y - bar_h), sc(bar_r), sc(bot_y)], radius=sc(12), fill=WHITE)

    # 两个加号：第一枚主蓝，第二枚浅蓝，形成“I++”的阅读节奏
    plus(d, 535, 512, 170, 68, ACCENT)
    plus(d, 760, 512, 170, 68, ACCENT_2)

    return bg


def main():
    os.makedirs(OUT, exist_ok=True)
    master = build_master()
    master1024 = master.resize((BASE, BASE), Image.LANCZOS)
    master1024.save(os.path.join(OUT, "impor-1024.png"), "PNG")

    for size in (512, 256, 128, 64, 48, 32, 16):
        master1024.resize((size, size), Image.LANCZOS).save(
            os.path.join(OUT, "impor-%d.png" % size), "PNG")

    master1024.save(os.path.join(OUT, "impor.ico"), format="ICO",
                    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                           (128, 128), (256, 256)])
    print("icon written to", OUT)


if __name__ == "__main__":
    main()
