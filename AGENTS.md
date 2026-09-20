# Agent 入口

成片、拼片、`--idea` 收编圣经、`--retry` 只走 Cursor 项目 Skill [`.cursor/skills/montage-produce/SKILL.md`](.cursor/skills/montage-produce/SKILL.md) 与 [`docs/skills/meta/produce.md`](docs/skills/meta/produce.md)。不要裸调 `shot_runner`。

停点读 `artifacts/produce_progress.json`：认 `status`，下一步用当前 `python -m montage` 加上 `next.argv` 里从 `produce` 起的参数（不要照抄 `argv[0]` 的解释器路径）。`await_bible` / `await_sample` / `await_cast` 等人点头再跑；不要见 `next` 就立刻 exec。

**接管项目启动三读（V40）**：项目内 `PROGRESS_TRACKER.md`（静态手册，禁改写重生成）→ `produce_progress.json`（机器事实，"下一步"唯一依据）→ `STATUS.md`（前人手账）。收尾把跨会话值得留的写 `STATUS.md` ≤5 行（用户拍板/改过哪些镜/坑），不写流水账。

**多角色评审纪律**（详见 `docs/ROLES.md`）：每轮 REVISE 必须 `review_logger record`（V21，不 record = 无效轮）；子 Agent 关卡首审 record 带 `phase=first_pass` 不占 4 轮额度（V36 定案）；停点汇报并读 `artifacts/REVIEW.md` + review_log 摘要（V24）。特效指导（P0-8）：点位一点位二与美术/动作并行换帽，`vfx[]` 只写 bible（细则 `docs/VFX_DIRECTOR.md`）；compile 自带确定性 vfx 自审。

**重编译强制条款（V23/V29/V37，P0 级）**：`await_shots` / `await_frames` / `await_final_prompt` / `await_clips` 状态下改 `series_bible.json` 后**禁止直接 `--resume`**——先确认 status 为 `await_*`，`python -m montage run <dir> idea_developer --input inputs.json`（inputs 仅 `{"operation":"compile"}`，bible 由工具直读），再 `--resume`。改 `scene_plan.json` 不受限。

无人值守设 `MONTAGE_HEADLESS=1`，或用户明确要求时用 `--review none`（`--idea` 路径仍不生成画面）。允许/禁止表只在 Skill 页。改 ffmpeg / 工具实现时忽略本页。

出错率经验见 `docs/AGENT_GUIDE.md`「外壳与出错率」；那不是 SLA。
