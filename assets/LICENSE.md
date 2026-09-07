# assets/ 许可证与署名要求

> 使用任何资产前请阅读本条。**不同子库许可证不同**，检索结果中的 `license` 字段
> 是权威依据；`asset_retriever` 会原样返回，成片发布前请核对。

## 1. `luts/` — 本仓库原创（MIT）

`scripts/make_luts.py` 生成的 `.cube` 调色表为程序化生成的原创内容，随本仓库以
MIT License 分发，可自由商用、修改、再分发（保留 MIT 声明即可）。

## 2. `sfx/` — Sonniss Game Audio GDC Bundle

- 官网：https://sonniss.com/gameaudiogdc/（2015-2024 各年度包免费下载）
- 许可性质：官方声明音效可用于**商业项目**（游戏/影视/视频/应用等），无需署名。
  精确条款以 Sonniss 官网各年度包页面为准。
- 本仓库不直接分发音效文件（体积约 200GB+），仅提供 `sfx/INDEX.md` 索引与下载指引。
- **引用约定**：成片使用 Sonniss 音效时，建议在 credits 中标注
  `Sound effects: Sonniss GDC Game Audio Bundle (sonniss.com)`（非强制，属良好实践）。

## 3. `bgm/` — 两种许可证并存

| 来源 | 许可证 | 署名要求 | 说明 |
|------|--------|----------|------|
| [FreePD](https://freepd.cn/) | CC0 1.0（公有领域贡献） | 无需署名 | 可自由商用/修改/再分发 |
| [incompetech](https://incompetech.com/)（Kevin MacLeod） | CC BY 3.0 | **必须署名**：INDEX `attribution`（如 `Music: Carefree by Kevin MacLeod (incompetech.com) — CC BY 3.0`） | 可商用，可修改 |

`bgm/INDEX.md` / `sfx/INDEX.md` 每条都标注 `license`。**CC-BY 条目在成片 credits
必须带署名**（用 `attribution` 字段）。已钉 `source_url` 的是 HEAD 验证直链：
MacLeod 五首 BGM、Wikimedia 雨声（PD）与心跳（CC BY 3.0）、BigSoundBank 脚步/门（CC0）。
远程换货（`asset_retriever remote=true`）下到项目 `assets/`，不写仓库 INDEX。

## 4. `fonts/` — 思源黑体（SIL OFL 1.1）

- 来源：https://github.com/adobe-fonts/source-han-sans （Adobe × Google 联合发布）
- 许可证：SIL Open Font License 1.1
  - ✅ 免费商用、嵌入程序/文档、修改、再分发
  - 📌 修改后发布的字体不得使用原名（需改名），再分发须保留 OFL 协议文本
  - 📌 字体本身不可单独出售（可随软件/成片捆绑分发）
- 字幕烧录用 `SourceHanSansSC-Regular.otf`（正文）与 `-Bold`（强调）即可覆盖绝大多数场景。

## 署名清单模板（成片 credits）

```
字体：思源黑体（SIL OFL 1.1，Adobe × Google）
音效：Sonniss GDC Game Audio Bundle（sonniss.com）
音乐：Carefree by Kevin MacLeod（incompetech.com，CC BY 3.0）   ← 仅 CC-BY 条目；以 INDEX attribution 为准
```
