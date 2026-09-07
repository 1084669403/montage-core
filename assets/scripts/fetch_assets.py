"""fetch_assets — 开源媒体资产获取指引（不自动下载超大文件）。

二进制资产（音效/音乐/字体）体积大且来自外部站点，本脚本**只打印下载指引**：
给出精确来源 URL 与落盘路径，由人工/后续工具完成下载；LUT 为仓库内置（无需下载）。

用法：
    python assets/scripts/fetch_assets.py            # 全部指引
    python assets/scripts/fetch_assets.py --sfx      # 仅音效
    python assets/scripts/fetch_assets.py --bgm      # 仅音乐
    python assets/scripts/fetch_assets.py --fonts    # 仅字体
    python assets/scripts/fetch_assets.py --list     # 列出所有待下载条目（含 id/file）

落盘后，asset_retriever 检索结果中的 available 自动变为 True。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # montage-core 根
ASSETS = ROOT / "assets"

GUIDES: dict[str, list[tuple[str, str]]] = {
    "sfx": [
        ("Sonniss GDC Game Audio Bundle（2015-2024 年度包，全部免费）",
         "https://sonniss.com/gameaudiogdc/"),
        ("下载后按 assets/sfx/INDEX.md 的 file 字段命名放入 assets/sfx/",
         "例：assets/sfx/rain-heavy.ogg"),
    ],
    "bgm": [
        ("FreePD（CC0 公有领域音乐，无需署名）", "https://freepd.cn/"),
        ("incompetech / Kevin MacLeod（CC BY 3.0，需署名）", "https://incompetech.com/"),
        ("下载后按 assets/bgm/INDEX.md 的 file 字段命名放入 assets/bgm/",
         "例：assets/bgm/tense-pulse.mp3"),
    ],
    "fonts": [
        ("思源黑体 Source Han Sans（SIL OFL 1.1）", "https://github.com/adobe-fonts/source-han-sans/releases"),
        ("解压后放入 assets/fonts/（SourceHanSansSC-Regular.otf / -Bold.otf）",
         "详见 assets/fonts/README.md"),
    ],
    "luts": [
        ("调色 LUT 为仓库内置（assets/luts/*.cube），无需下载",
         "python scripts/make_luts.py 可重新生成"),
    ],
}


def _pending_entries() -> list[dict[str, str]]:
    """扫描 assets/*/INDEX.md 列出 file 尚未落盘的条目。"""
    import re

    pending: list[dict[str, str]] = []
    key_re = re.compile(r"^([a-z_]+):\s*(.*)$")
    for index in sorted(ASSETS.glob("*/INDEX.md")):
        category = index.parent.name
        current: dict[str, str] | None = None

        def flush() -> None:
            if current is None or "id" not in current:
                return
            file_val = current.get("file", "").strip()
            if file_val and not file_val.startswith("("):
                target = ASSETS / file_val
                if not target.is_file():
                    pending.append(
                        {
                            "id": current["id"].strip("`"),
                            "category": category,
                            "file": file_val,
                            "source_url": current.get("source_url", "").strip().strip('"'),
                        }
                    )

        for raw in index.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip()
            if line.startswith("- id:"):
                flush()
                current = {"id": line[len("- id:"):].strip()}
                continue
            if current is None or not line.startswith("  "):
                continue
            m = key_re.match(line.strip())
            if m:
                current[m.group(1)] = m.group(2).strip()
        flush()
    return pending


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        for p in _pending_entries():
            extra = f"  {p['source_url']}" if p.get("source_url") else ""
            print(f"[{p['category']}] {p['id']}  ->  assets/{p['file']}{extra}")
        print(f"\n共 {len(_pending_entries())} 个待下载条目")
        return 0

    want = {a.lstrip("-") for a in argv if a.startswith("--")}
    if not want:
        want = set(GUIDES)

    for cat in GUIDES:
        if cat not in want:
            continue
        print(f"\n=== {cat.upper()} ===")
        for label, url in GUIDES[cat]:
            print(f"  {label}: {url}")

    pending = [p for p in _pending_entries() if not want or p["category"] in want]
    if pending:
        print(f"\n待下载 {len(pending)} 条（--list 查看明细）")
    print("\n提示：下载后 asset_retriever 的 available 自动变为 True；")
    print("有 source_url 的条目用 asset_retriever operation=resolve；CC-BY 必须署名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
