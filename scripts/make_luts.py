"""make_luts — 生成原创基础调色 LUT（.cube，3D 17³）。

零依赖（纯 Python stdlib），生成 assets/luts/*.cube，供 ffmpeg `lut3d` 滤镜使用
（见 montage/compose/ffmpeg_engine.py 的 apply_lut）。

调色表为**程序化生成的原创内容**（MIT，随本仓库分发）：每个调色是一个可读的
像素级映射函数，按亮度/通道做曲线与混色，不复制任何第三方 LUT。

用法：
    python scripts/make_luts.py            # 生成全部（覆盖 assets/luts/*.cube）
    python scripts/make_luts.py teal-orange   # 只生成指定调色

输出：assets/luts/<id>.cube（TITLE / LUT_3D_SIZE 17 / DOMAIN 0-1 / RGB 行）
"""

from __future__ import annotations

import sys
from pathlib import Path

LUT_SIZE = 17
OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "luts"

# ---------------------------------------------------------------------------
# 像素映射函数：输入 r/g/b ∈ [0,1]，输出 (r', g', b') ∈ [0,1]
# ---------------------------------------------------------------------------


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _lum(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def teal_orange(r: float, g: float, b: float) -> tuple[float, float, float]:
    """青橙对比（电影感标配）：阴影偏青，高光偏橙。"""
    lum = _lum(r, g, b)
    shadow_w = 1.0 - lum  # 暗部权重
    hi_w = lum            # 亮部权重
    r2 = r + 0.07 * hi_w - 0.05 * shadow_w
    g2 = g + 0.02 * shadow_w
    b2 = b + 0.07 * shadow_w - 0.07 * hi_w
    return _clamp(r2), _clamp(g2), _clamp(b2)


def dark_moody(r: float, g: float, b: float) -> tuple[float, float, float]:
    """暗黑电影感：整体压暗、降低饱和、阴影更深。"""
    lum = _lum(r, g, b)
    out = lum ** 1.12
    # 向灰色混合 25% 降饱和
    r2 = r * 0.75 + out * 0.25
    g2 = g * 0.75 + out * 0.25
    b2 = b * 0.75 + out * 0.25
    # 阴影进一步下压
    r2 = r2 ** 1.06
    g2 = g2 ** 1.06
    b2 = b2 ** 1.06
    return _clamp(r2), _clamp(g2), _clamp(b2)


def warm_film(r: float, g: float, b: float) -> tuple[float, float, float]:
    """胶片暖调：高光暖黄、黑色轻微提升（fade）、轻微褪色。"""
    lum = _lum(r, g, b)
    hi_w = lum
    r2 = r + 0.06 * hi_w
    b2 = b - 0.05 * hi_w
    # fade blacks：整体抬升 4%（胶片感）
    r2 = r2 * 0.96 + 0.04
    g2 = g * 0.96 + 0.04
    b2 = b2 * 0.96 + 0.04
    # 轻微褪色（向灰色混 8%）
    out = _lum(r2, g2, b2)
    return _clamp(r2 * 0.92 + out * 0.08), _clamp(g2 * 0.92 + out * 0.08), _clamp(b2 * 0.92 + out * 0.08)


def cool_clean(r: float, g: float, b: float) -> tuple[float, float, float]:
    """日系清冷：冷调、高明度、低对比、轻微去饱和。"""
    lum = _lum(r, g, b)
    # 明度提升（gamma < 1）
    out = lum ** 0.94
    # 向灰色混 18% 降饱和，整体偏冷
    r2 = (r * 0.82 + out * 0.18) - 0.02
    g2 = g * 0.82 + out * 0.18
    b2 = (b * 0.82 + out * 0.18) + 0.03
    return _clamp(r2), _clamp(g2), _clamp(b2)


def bw_high_contrast(r: float, g: float, b: float) -> tuple[float, float, float]:
    """黑白高反差：去色 + S 曲线。"""
    lum = _lum(r, g, b)
    if lum < 0.5:
        out = lum ** 1.35
    else:
        out = 1.0 - (1.0 - lum) ** 1.35
    # 强化黑白两端
    out = _clamp((out - 0.5) * 1.25 + 0.5)
    return out, out, out


def muted_documentary(r: float, g: float, b: float) -> tuple[float, float, float]:
    """纪实低饱和：中性色温、保细节、轻微降对比。"""
    lum = _lum(r, g, b)
    # 向灰色混 30%（纪实感）
    r2 = r * 0.7 + lum * 0.3
    g2 = g * 0.7 + lum * 0.3
    b2 = b * 0.7 + lum * 0.3
    # 轻微降对比（压缩到 0.92 范围）
    r2 = (r2 - 0.5) * 0.92 + 0.5
    g2 = (g2 - 0.5) * 0.92 + 0.5
    b2 = (b2 - 0.5) * 0.92 + 0.5
    return _clamp(r2), _clamp(g2), _clamp(b2)


# 调色注册表：id -> (显示名, 映射函数)
LUTS: dict[str, tuple[str, object]] = {
    "teal-orange": ("青橙对比（电影感标配）", teal_orange),
    "dark-moody": ("暗黑电影感（悬疑/压迫）", dark_moody),
    "warm-film": ("胶片暖调（复古/怀旧）", warm_film),
    "cool-clean": ("日系清冷（治愈/清新）", cool_clean),
    "bw-high-contrast": ("黑白高反差（黑白片）", bw_high_contrast),
    "muted-documentary": ("纪实低饱和（纪录片）", muted_documentary),
}


def write_cube(lut_id: str, title: str, fn: object) -> Path:
    """生成单个 .cube 文件（r 外层循环、b 最内层，标准 .cube 顺序）。

    TITLE 用 ASCII（ffmpeg/第三方解析器对非 ASCII TITLE 兼容性不一），
    中文描述只出现在文件注释行（``# ...``），解析器忽略注释。
    """
    out = OUT_DIR / f"{lut_id}.cube"
    out.parent.mkdir(parents=True, exist_ok=True)
    ascii_title = {
        "teal-orange": "teal-orange cinematic contrast",
        "dark-moody": "dark moody suspense",
        "warm-film": "warm film vintage",
        "cool-clean": "cool clean japanese",
        "bw-high-contrast": "black-white high contrast",
        "muted-documentary": "muted documentary",
    }.get(lut_id, lut_id)
    lines = [
        f"# montage LUT: {lut_id} — {title}（原创，MIT）",
        f'TITLE "montage {ascii_title}"',
        f"LUT_3D_SIZE {LUT_SIZE}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]
    step = 1.0 / (LUT_SIZE - 1)
    for ri in range(LUT_SIZE):
        for gi in range(LUT_SIZE):
            for bi in range(LUT_SIZE):
                r, g, b = fn(ri * step, gi * step, bi * step)
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    targets = argv or list(LUTS)
    written: list[str] = []
    for lut_id in targets:
        if lut_id not in LUTS:
            print(f"未知调色: {lut_id}（可选: {', '.join(LUTS)}）")
            return 1
        title, fn = LUTS[lut_id]
        path = write_cube(lut_id, title, fn)
        written.append(str(path))
    print(f"生成 {len(written)} 个 LUT:")
    for p in written:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
