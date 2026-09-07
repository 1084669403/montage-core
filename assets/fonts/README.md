# fonts/ — 字幕字体（思源黑体，SIL OFL 1.1）

> 成片字幕的字体选择直接影响观感。本仓库推荐**思源黑体（Source Han Sans）**：
> Adobe × Google 联合开源，SIL OFL 1.1，可免费商用、可嵌入、可修改。

## 下载与安装

1. 前往发布页：https://github.com/adobe-fonts/source-han-sans/releases
2. 下载 `SourceHanSansSC.zip`（简体中文子集，体积最小）或完整版。
3. 解压后放入本目录：`assets/fonts/SourceHanSansSC-Regular.otf`（正文）、
   `SourceHanSansSC-Bold.otf`（强调）。
4. 也可用包管理器：`winget install` / 手动安装到系统字体目录。

## 使用（ffmpeg 字幕烧录）

- **ASS 样式**（推荐，见 `montage/compose/ffmpeg_engine.py` 的 `burn_subtitles`）：
  在 ASS 头部 `[V4+ Styles]` 中指定 `Fontname: Source Han Sans SC`，并确保字体
  已安装（Windows: `C:\Windows\Fonts`；Linux: `~/.fonts` 或 `fc-cache`）。
- **fallback**：ffmpeg 的 `subtitles` 滤镜使用 libass，找不到字体时自动回退，
  但回退字体观感差——务必安装后再烧录。

## 替代字体（同为 OFL/可商用）

| 字体 | 风格 | 来源 |
|------|------|------|
| 思源宋体（Source Han Serif） | 衬线/文艺片 | adobe-fonts/source-han-serif |
| 得意黑（Smiley Sans） | 现代标题/强调 | atelier-anchor/smiley-sans |
| 阿里巴巴普惠体 | 通用正文 | 阿里巴巴（免费商用授权） |

## 许可证要点（SIL OFL 1.1）

- ✅ 免费商用、嵌入、修改、再分发（可随成片/程序分发字体文件）
- 📌 修改后发布的字体需改名（不得用原名）
- 📌 字体不得单独出售
- 完整条款见 https://scripts.sil.org/OFL （字体文件内亦含 LICENSE.txt）
