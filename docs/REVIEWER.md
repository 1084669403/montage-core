# REVIEWER — 阶段产物自审协议（原创）

> 给"导演"（LLM Agent）在每个门禁阶段 checkpoint 之前的**质量门禁协议**。
> 借鉴行业通行的"准确-完整-建设性"审查三要素与严重度分级共识，文本为原创。
> 配合确定性工具使用：schema 校验（ArtifactStore）、`script_validator`、
> `edit_advisor`、playbook 的 `quality_rules`。
>
> **多角色底座**：本协议是所有角色（编剧/导演/美术指导/动作指导）审查的公共底座；
> 角色分工/轮次规则/record 纪律/振荡检测见 [ROLES.md](ROLES.md)——finding 须带
> `role` 字段（screenwriter/director/art_director/action_director），评审记录走
> `review_logger`（artifacts/review_log.jsonl），不再只写决策日志。

## 何时使用

每个 gated 阶段（proposal / script / scene_plan / assets / publish）产出规范产物后、
写 checkpoint 之前，**必须**执行一次审查。审查质量决定成片是否值得看——跳过审查
等于放弃质量门禁。

## 审查三要素

**准确（Accurate）**：每个发现必须指向具体产物字段/镜头 id/可见画面帧。
禁止凭空批评——指不出位置就是在猜。
- ✅ "sc02 的 description 写'她感到害怕'，心理状态不可拍（命中 script_validator 心理词表）"
- ❌ "这场景写得不好"

**完整（Complete）**：发现一类错误就扫完全部同类再返回。抓到一处台词超长，
就检查所有镜头的台词预算；发现一个角色引用断裂，就核对全部 character_ids。

**建设性（Constructive）**：每条 critical 发现**必须**给出具体修复方案
（替换文本/字段值/操作步骤）。给不出修复方案就降级为 investigation。
- ✅ "sc02 改写为：'她后退两步，手扶墙，指节发白'（可见动作 + 可见细节）"
- ❌ "sc02 有问题，重写"（无方案）

## 严重度分级

| 级别 | 含义 | 处理 |
|------|------|------|
| **critical** | 产物损坏/不完整/违背硬约束，必须修复后才能继续；**必须带 proposed_fix** | 阻塞 |
| **suggestion** | 显著影响质量但不阻塞；**必须带 proposed_change** | 建议修复 |
| **nitpick** | 锦上添花的小打磨 | 可选 |
| **investigation** | 真实疑点但暂时指不出修法 | 记录，不阻塞 |

## 协议步骤

1. **Schema 校验**（非协商项）：产物必须通过 `ArtifactStore` 的 JSON Schema 校验；
   失败 = critical，先修再往下。
2. **对产物逐项审查**：按本阶段要交付的字段逐项评估（剧本看人物卡/对白预算/
   节拍结构；分镜看可拍性/角色引用/镜头语言；资产看提示词完整性）。
3. **跑确定性工具**：`script_validator`（对白预算/可拍性/人物引用）、
   `edit_advisor`（转场建议）——工具发现的问题直接转为 finding。
4. **对照 playbook**：若 proposal 已锁定 playbook，核对 quality_rules
   （每屏颜色、最短停留、转场白名单、人物默认外观）。
5. **给出决策**：
   - 0 条 critical → **PASS**：写 checkpoint，suggestion/nitpick 记入决策日志。
   - ≥1 条 critical → **REVISE**：修复全部 critical 后重审，**最多 2 轮**。
   - 2 轮后仍有关键问题 → **PASS WITH WARNINGS**：带未解决问题继续，记录在案，
     绝不无限阻塞。
6. **记录审查**：结果写入决策日志（category=review, subject=<stage>），格式：

```
## Review: <stage> — Round <N>
**Decision:** PASS / REVISE / PASS_WITH_WARNINGS
1. [CRITICAL] <字段/镜头>：问题 → 修复方案
2. [SUGGESTION] ...
```

## 与门禁的关系

- 审查**不替代** `CheckpointStore` 的人类审批门禁：REVISE 通过的产物仍需
  `human_approved=True` 才能 completed。
- 审查是"导演自审"，人类审批是"制片人终审"——两道关卡目的不同，都要走。
