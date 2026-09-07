# assets/ — 开源媒体资产库（音效 / 音乐 / LUT / 字体）

> 二进制资产（音频/字体/调色表）体积大，**不随 git 仓库分发**。本目录存放：
> ① 每个子库的 `INDEX.md` 元数据索引（`lib/asset_catalog.py` 可检索）；
> ② 许可证与来源说明；③ 获取脚本（`assets/scripts/fetch_assets.py`）。

## 子库总览

| 子库 | 内容 | 来源 | 许可证 | 状态 |
|------|------|------|--------|------|
| `sfx/` | 音效（雨/脚步/爆炸/氛围等） | [Sonniss GDC Game Audio Bundle](https://sonniss.com/gameaudiogdc/)（2015-2024 全部包免费） | 免版税可商用（见 LICENSE.md） | 索引就绪，需下载 |
| `bgm/` | 背景音乐（氛围/情绪/节奏） | [FreePD](https://freepd.cn/)（CC0 公有领域，无需署名）＋ [incompetech/Kevin MacLeod](https://incompetech.com/)（CC-BY，需署名） | CC0 / CC-BY | 索引就绪，需下载 |
| `luts/` | 调色 LUT（.cube，青橙/暗黑/胶片等） | 本仓库原创生成（`scripts/make_luts.py`，零版权风险） | MIT | ✅ 随仓库分发 |
| `fonts/` | 字幕字体（思源黑体等） | [思源黑体 Source Han Sans](https://github.com/adobe-fonts/source-han-sans)（Adobe/Google） | SIL OFL 1.1（可商用可嵌入） | 指引就绪，需下载 |

## 使用方式（Agent / 人类）

1. **检索**：调用 `asset_retriever` 工具（capability=asset_retrieval）按情绪/场景/节奏查
   `assets/*/INDEX.md`，返回条目（含 `file`、`source`、`license`）。
2. **落盘**：主路径 `asset_retriever operation=resolve`（`produce` 内由 `soundtrack_planner resolve=true` 调用，钉选曲复制进项目 `assets/music/`）。批量预拉仓库曲库才用 `python assets/scripts/fetch_assets.py --sfx --bgm --fonts`。
3. **应用**：
   - 音效/音乐：`ffmpeg_compose` 的 `mix_audio` / 新增 `place_audio`（按时间轴放置）；
   - LUT：`ffmpeg_compose` 的 `apply_lut`（lut3d 滤镜）；
   - 字体：`burn_subtitles` 指定 ASS 样式字体。

## 许可证矩阵（详见 LICENSE.md）

- `assets/scripts/make_luts.py` 生成的 LUT：MIT（本仓库原创）。
- Sonniss GDC 包：官方声明可用于商业项目（含游戏/影视/视频），无需署名（以官方页面条款为准）。
- FreePD 音乐：CC0 1.0 公有领域贡献，无需署名，可商用。
- incompetech（Kevin MacLeod）：**CC BY 3.0**，需署名（作者 Kevin MacLeod / incompetech.com）。
- 思源黑体：SIL Open Font License 1.1，可免费商用、可嵌入程序、可修改（修改后需保留 OFL 声明）。

## 目录结构约定

```
assets/
├── README.md          # 本文件
├── LICENSE.md         # 许可证与署名要求汇总
├── INDEX.md           # 资产总索引（可选，供 asset_catalog 快速浏览）
├── sfx/INDEX.md       # 音效条目（file: 相对路径，下载后可用）
├── bgm/INDEX.md       # 音乐条目
├── luts/              # 原创 .cube 调色表（随仓库分发）
├── fonts/README.md    # 字体下载指引
└── scripts/
    ├── fetch_assets.py   # 下载指引脚本
    └── make_luts.py      # 原创 LUT 生成器（生成 assets/luts/*.cube）
```
