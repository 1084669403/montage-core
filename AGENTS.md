# Agent 入口

成片、拼片、`--idea` 收编圣经、`--retry` 只走 Cursor 项目 Skill [`.cursor/skills/montage-produce/SKILL.md`](.cursor/skills/montage-produce/SKILL.md) 与 [`docs/skills/meta/produce.md`](docs/skills/meta/produce.md)。不要裸调 `shot_runner`。

停点读 `artifacts/produce_progress.json`：认 `status`，下一步用当前 `python -m montage` 加上 `next.argv` 里从 `produce` 起的参数（不要照抄 `argv[0]` 的解释器路径）。`await_bible` / `await_sample` / `await_cast` 等人点头再跑；不要见 `next` 就立刻 exec。

无人值守设 `MONTAGE_HEADLESS=1`，或用户明确要求时用 `--review none`（`--idea` 路径仍不生成画面）。允许/禁止表只在 Skill 页。改 ffmpeg / 工具实现时忽略本页。

出错率经验见 `docs/AGENT_GUIDE.md`「外壳与出错率」；那不是 SLA。
