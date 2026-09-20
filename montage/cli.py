"""cli — 命令行入口。

命令：
  montage doctor [--pipeline NAME] [--project DIR] [--json]
  montage tools
  montage init <id> --title "..." [--root DIR]
  montage status <project_dir>
  montage check <project_dir> <stage> --completed --approved [--caps ...] [--caps-strict]
  montage run <project_dir> <tool> --input inputs.json
  montage produce <project_dir> [--resume] [--skip-export] [--strict-audio] [--keep-scratch] [--tts]
  montage produce <project_dir> --idea "讲量子计算" [--review bible]
  montage auto_edit <project_dir> --video/--clips/--audio-only --style ...
  montage delivery_report <project_dir> [--summary]
  montage srt_rebuild <project_dir> [--write]
  montage webui [--port 8399]

本文件为全新原创代码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from montage.engine.project import init_project
from montage.engine.stages import STAGE_ORDER, CheckpointStore, StageStatus
from montage.registry import ToolRegistry


def _discover() -> ToolRegistry:
    reg = ToolRegistry()
    reg.discover()
    return reg


def _doctor_payload(reg: ToolRegistry, args: argparse.Namespace) -> dict:
    summary = reg.provider_menu_summary()
    try:
        import jsonschema  # noqa: F401
        has_schema = True
    except ImportError:
        has_schema = False
    from montage.engine.envfile import injected_key_count
    from montage.providers.agnes_usage import usage_snapshot
    from montage.providers.capabilities import doctor_video_surfaces

    payload: dict = {
        "capabilities": summary["capabilities"],
        "setup_offers": summary.get("setup_offers") or [],
        "import_errors": summary.get("import_errors") or [],
        "video_surfaces": doctor_video_surfaces(),
        "agnes_usage": usage_snapshot(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "jsonschema": has_schema,
        "dotenv_injected": injected_key_count(),
    }
    if getattr(args, "pipeline", None) or getattr(args, "project", None):
        from montage.engine.policy import (
            skill_paths_for_pipeline,
            skill_paths_for_project,
        )

        payload["skills"] = (
            skill_paths_for_project(args.project)
            if args.project
            else skill_paths_for_pipeline(args.pipeline)
        )
    return payload


def _format_duration_policy(policy: dict) -> str:
    kind = str((policy or {}).get("kind") or "")
    if kind == "enum":
        values = (policy or {}).get("values") or []
        return "/".join(str(v) + "s" for v in values) or "enum"
    if kind == "range":
        return f"{policy.get('min')}–{policy.get('max')}s"
    if kind == "none":
        return "none"
    return kind or "-"


def _cmd_doctor(reg: ToolRegistry, args: argparse.Namespace) -> int:
    payload = _doctor_payload(reg, args)
    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    print("== 工具能力菜单 ==")
    for cap in payload["capabilities"]:
        mark = "ok" if cap["configured"] else "--"
        print(f"  {mark} {cap['capability']}: {cap['configured']}/{cap['total']}")
        for tool in cap["tools"]:
            print(f"      - {tool['name']} ({tool['provider']}) [{tool['status']}]")
    for offer in payload["setup_offers"]:
        print(f"  待配置: {offer['tool']} → {offer['hint']}")
    print("== 视频 API 面 ==")
    for surface in payload.get("video_surfaces") or []:
        wired = "接线" if surface.get("wired") else "未接线"
        audio = "audio=yes" if surface.get("native_audio") else "audio=no"
        multi = "multi=yes" if surface.get("multi_shot") else "multi=no"
        policy = surface.get("duration_policy") or {}
        dur = _format_duration_policy(policy)
        print(
            f"  {surface.get('api_id')}  {surface.get('label')}  [{wired}]  "
            f"{audio}  {multi}  {dur}"
        )
    errors = payload.get("import_errors") or []
    if errors:
        print("== 发现时导入失败 ==")
        for item in errors:
            print(f"  {item}")
    usage = payload.get("agnes_usage") or {}
    tier = str(usage.get("tier") or "default")
    if usage.get("declared"):
        print(f"agnes 访问档位: {tier}")
    else:
        print("agnes 访问档位: default（免费/默认档，未声明 Token Plan）")
    if usage.get("image_limit") is not None:
        print(
            f"  今日图片: {usage.get('images', 0):g}/{usage['image_limit']:g}"
            f"（余 {usage.get('image_remaining', 0):g}）"
        )
    if usage.get("video_limit") is not None:
        print(
            f"  今日视频: {usage.get('video_seconds', 0):g}/{usage['video_limit']:g} 秒"
            f"（余 {usage.get('video_remaining', 0):g}）"
        )
    print(f"ffmpeg: {'OK' if payload['ffmpeg'] else 'MISSING'}")
    print(f"jsonschema: {'OK' if payload['jsonschema'] else 'MISSING（核心依赖，pip install -e .）'}")
    print(f"dotenv: 已从 .env 注入 {payload['dotenv_injected']} 个键")
    skills = payload.get("skills") or []
    if skills:
        print("== 导演技能 ==")
        for p in skills:
            print(f"  {p}")
    return 0


def _cmd_tools(reg: ToolRegistry) -> int:
    for cap, names in reg.capability_catalog().items():
        print(f"[{cap}]")
        for name in names:
            print(f"  {name}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    proj = init_project(root, args.project_id, args.title, args.pipeline)
    print(f"项目已初始化: {proj}")
    print(f"  project.json  {proj / 'project.json'}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = CheckpointStore(Path(args.project_dir))
    for stage in STAGE_ORDER:
        cp = store.read(stage)
        status = cp.status if cp else StageStatus.PENDING.value
        mark = {"completed": "✓", "in_progress": "◐", "awaiting_human": "⏸"}.get(status, "·")
        print(f"  {mark} {stage:<12} {status}")
    nxt = store.next_stage()
    print(f"\n下一阶段: {nxt or '（全部完成）'}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from montage.engine.gates import (
        GateError,
        check_tool_allowlist,
        validate_completion,
    )

    store = CheckpointStore(Path(args.project_dir))

    # 产物门禁：completed 前校验 produces 产物存在且过 schema（只拦 completed）
    if args.completed:
        try:
            gate = validate_completion(args.project_dir, args.stage)
        except GateError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        for warn in gate["warnings"]:
            print(f"提示: {warn}")

    # 工具白名单：--caps 使用的能力族须在阶段允许范围内；--caps-strict 则失败
    if args.caps:
        warns = check_tool_allowlist(
            args.stage, [c.strip() for c in args.caps.split(",") if c.strip()],
            project_dir=args.project_dir,
        )
        for warn in warns:
            print(f"提示: {warn}")
        if args.caps_strict and warns:
            print("错误: 能力族白名单校验失败（--caps-strict）", file=sys.stderr)
            return 2

    try:
        store.write(
            args.stage,
            StageStatus.COMPLETED.value if args.completed else StageStatus.IN_PROGRESS.value,
            human_approved=args.approved,
            approved_by="human" if args.approved else "",
            artifact=args.artifact,
        )
        print(f"{args.stage}: checkpoint 已写入")
        return 0
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


def _load_vfx_arg(raw: str) -> dict[str, list]:
    """解析 ``--vfx``：JSON 对象或指向 JSON 文件的路径。

    形状 ``{shot_id: [vfx_item, ...]}``；值也可直接是单条 vfx dict（自动包成列表）。
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("空字符串")
    path = Path(text)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TypeError("必须是 JSON 对象（shot_id → vfx[]）")
    out: dict[str, list] = {}
    for key, val in payload.items():
        sid = str(key or "").strip()
        if not sid:
            continue
        if isinstance(val, dict):
            out[sid] = [val]
        elif isinstance(val, list):
            out[sid] = list(val)
        else:
            raise TypeError(f"{sid!r} 的值必须是 vfx 对象或数组")
    if not out:
        raise ValueError("没有有效的 shot_id 条目")
    return out


def _cmd_auto_edit(args: argparse.Namespace) -> int:
    """快速路径：plan → 可选 replan → preview/render。不调用 init_project。"""
    from montage.style_packs import STYLE_PACKS, list_style_packs
    from montage.tools.auto_edit import AutoEdit

    if args.overrides:
        try:
            overrides = json.loads(args.overrides)
        except json.JSONDecodeError as exc:
            print(f"错误: --overrides 不是合法 JSON（{exc}）", file=sys.stderr)
            return 2
        if not isinstance(overrides, dict):
            print("错误: --overrides 必须是 JSON 对象", file=sys.stderr)
            return 2
    else:
        overrides = {}

    if args.style not in STYLE_PACKS:
        known = ", ".join(p["id"] for p in list_style_packs())
        print(f"错误: 未知风格包 {args.style!r}（可选: {known}）", file=sys.stderr)
        return 2

    present = sum(bool(x) for x in (args.video, args.clips, args.audio_only))
    if present != 1:
        print("错误: --video / --clips / --audio-only 必须三选一", file=sys.stderr)
        return 2

    tool = AutoEdit()
    project_dir = str(Path(args.project_dir))
    base: dict = {
        "project_dir": project_dir,
        "style": args.style,
        "has_bgm": bool(args.has_bgm),
        "is_speech": bool(args.is_speech),
        "language": args.language,
        "resume": not args.no_resume,
    }
    if args.video:
        base["video"] = args.video
    elif args.clips:
        base["clips"] = list(args.clips)
    else:
        base["audio_only"] = args.audio_only
    if args.profile:
        base["profile"] = args.profile
    if args.target_duration is not None:
        base["target_duration"] = args.target_duration
    if args.no_scene_index:
        base["use_scene_index"] = False
    elif args.scene_index:
        base["scene_index_path"] = args.scene_index
    if args.bpm:
        base["bpm"] = float(args.bpm)
    if args.beats_per_bar and int(args.beats_per_bar) != 4:
        base["beats_per_bar"] = int(args.beats_per_bar)
    if args.no_beat_cuts:
        base["beat_cuts"] = False
    if args.reason:
        base["reason"] = args.reason
    pack_keys = {"lut", "transitions", "pacing", "output_profile", "bind_playbook"}
    pack_over = {k: overrides[k] for k in pack_keys if k in overrides}
    replan_over = {k: v for k, v in overrides.items() if k not in pack_keys}

    # P0-8：--vfx 作者面 → 转成 replan overrides（shot_id → {vfx: [...]}）
    if getattr(args, "vfx", None):
        try:
            vfx_map = _load_vfx_arg(args.vfx)
        except (OSError, TypeError, json.JSONDecodeError, ValueError) as exc:
            print(f"错误: --vfx 解析失败（{exc}）", file=sys.stderr)
            return 2
        for sid, items in vfx_map.items():
            row = replan_over.setdefault(sid, {})
            if not isinstance(row, dict):
                print(f"错误: --overrides 与 --vfx 冲突于 {sid!r}", file=sys.stderr)
                return 2
            row["vfx"] = items

    plan_inputs = dict(base)
    plan_inputs["operation"] = "plan"
    if pack_over:
        plan_inputs["overrides"] = pack_over
    result = tool.execute(plan_inputs)
    if not result.success:
        print(f"错误: plan 失败 — {result.error}", file=sys.stderr)
        return 2
    print(f"plan: {result.data.get('path')}  (rev {result.data.get('rev')})")
    note = result.data.get("scene_index") or {}
    if note.get("used"):
        print(f"情节单元: {note.get('unit_count')} 个，叙事切点 {note.get('narrative_cuts')}，"
              f"落成断镜 {note.get('applied')}")
        if note.get("note"):
            print(f"  注意: {note['note']}")
    else:
        print(f"情节单元: 未启用（{note.get('reason') or '无匹配源'}）")

    beat = result.data.get("beat_cuts") or {}
    if beat.get("used"):
        bm = result.data.get("beat_map") or {}
        print(f"能量波切点(P0-5): bpm={bm.get('bpm')}（{bm.get('bpm_source')}）"
              f" 能量源={bm.get('energy_source')} 刀数={len(bm.get('cuts') or [])}")
        for row in bm.get("per_source") or []:
            name = Path(str(row.get("path"))).name
            if row.get("mode") == "replaced":
                print(f"  {name}: {row.get('cuts')} 刀（替换 {row.get('scene_cuts_before')} 个 scene 切点）")
            else:
                print(f"  {name}: 未接管（保留 {row.get('scene_cuts_before')} 个 scene 切点）")
        for w in bm.get("warnings") or []:
            print(f"  注意: {w}")
    elif beat.get("reason"):
        print(f"能量波切点(P0-5): 未启用（{beat['reason']}）")

    if replan_over:
        r2 = tool.execute({
            "operation": "replan",
            "project_dir": project_dir,
            "overrides": replan_over,
            "reason": args.reason or "CLI --overrides",
        })
        if not r2.success:
            print(f"错误: replan 失败 — {r2.error}", file=sys.stderr)
            return 2
        print(f"replan: rev {r2.data.get('rev')} changed={r2.data.get('changed')} "
              f"（{r2.data.get('diff_summary')}）")
        if not r2.data.get("recorded"):
            print("  （与上一版无实质差异，未新增版本）")

    render_inputs = dict(base)
    render_inputs["operation"] = "preview" if args.preview else "render"
    rendered = tool.execute(render_inputs)
    if not rendered.success:
        print(f"错误: {render_inputs['operation']} 失败 — {rendered.error}", file=sys.stderr)
        return 2
    steps = rendered.data.get("render_log") or []
    if steps:
        print("六步: " + " / ".join(f"{s.get('step')}={s.get('status')}" for s in steps))
    print(f"output: {rendered.data.get('output_path')}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """跑单个工具：stdout 一行 JSON（ToolResult），路径保持相对 cwd。"""
    from montage.engine.runtime import run_tool

    if args.input_path:
        try:
            payload = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"错误: 无法读取 --input（{exc}）", file=sys.stderr)
            return 2
    elif args.json_inline:
        try:
            payload = json.loads(args.json_inline)
        except json.JSONDecodeError as exc:
            print(f"错误: --json 不是合法 JSON（{exc}）", file=sys.stderr)
            return 2
    else:
        payload = {}
    if not isinstance(payload, dict):
        print("错误: 输入必须是 JSON 对象", file=sys.stderr)
        return 2
    payload["project_dir"] = str(Path(args.project_dir))
    reg = _discover()
    cls = reg.get(args.tool)
    if cls is None:
        print(f"错误: 未知工具 {args.tool!r}", file=sys.stderr)
        return 2
    result = run_tool(cls(), payload)
    print(json.dumps(
        {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "meta": result.meta,
            "cost_usd": result.cost_usd,
        },
        ensure_ascii=False,
        default=str,
    ))
    return 0 if result.success else 2


def _print_next(progress: dict | None) -> None:
    nxt = (progress or {}).get("next") if isinstance(progress, dict) else None
    if not isinstance(nxt, dict):
        return
    argv = nxt.get("argv") or []
    if not isinstance(argv, list) or "produce" not in argv:
        return
    idx = argv.index("produce")
    print("next: python -m montage " + " ".join(str(x) for x in argv[idx:]))


def _cmd_produce(args: argparse.Namespace) -> int:
    from montage.engine.produce import run_produce

    result = run_produce(
        args.project_dir,
        resume=bool(args.resume),
        skip_export=bool(args.skip_export),
        strict_audio=bool(args.strict_audio),
        keep_scratch=bool(args.keep_scratch),
        prune_exports=int(getattr(args, "prune_exports", 0) or 0),
        idea=getattr(args, "idea", None),
        review=str(getattr(args, "review", None) or "director"),
        tts=bool(getattr(args, "tts", False)),
        sample_hero=not (
            not getattr(args, "idea", None)
            and str(getattr(args, "review", None) or "director") == "none"
        ),
        trim_hero=bool(getattr(args, "trim_hero", False)),
        all_video=bool(getattr(args, "all_video", False)),
        skip_finish=bool(getattr(args, "skip_finish", False)),
        burn_subs=bool(getattr(args, "burn_subs", False)),
        profile=str(getattr(args, "profile", None) or ""),
        retry_ids=None if getattr(args, "retry", None) is None else [
            p.strip() for p in str(args.retry).split(",") if p.strip()
        ],
        retry_confirmed=bool(getattr(args, "yes", False)),
        season_concat=bool(getattr(args, "season_concat", False)),
        accept_degraded_vlm=bool(getattr(args, "accept_degraded_vlm", False)),
    )
    status = str((result.get("progress") or {}).get("status") or "")
    if result.get("success"):
        director_msg = {
            "await_setup": "produce: await_setup（确认需求卡后 --resume；改 series_bible.json，不要改 REVIEW.md）",
            "await_outline": "produce: await_outline（确认大纲后 --resume）",
            "await_design": "produce: await_design（确认设计表后 --resume；此后才定妆）",
            "await_cast": "produce: await_cast（确认全身照/四视图后 --resume；失败则 --retry <id> --resume）",
            "await_shots": "produce: await_shots（确认分镜后 --resume；此后才出关键帧）",
            "await_frames": "produce: await_frames（确认首帧后 --resume；失败则 --retry <id> --resume）",
            "await_final_prompt": "produce: await_final_prompt（确认最终提示词总览后 --resume 才生成；改提示词回 scene_plan / series_bible）",
            "await_clips": "produce: await_clips（确认单镜视频后 --resume；失败则 --retry <id> --resume）",
        }
        if status in director_msg:
            print(director_msg[status])
        elif status == "await_bible":
            print("produce: await_bible（改完 series_bible.json 后再 produce，不要 --idea）")
        elif status == "await_prompt":
            print("produce: await_prompt（提示词超 3000 字。要改圣经就改完后 produce，不要 --resume；不改则 --resume 走压缩兜底）")
        elif status == "await_sample":
            print("produce: await_sample（检查样品后 --resume 继续；这不是人审）")
        elif status == "await_retry":
            print("produce: await_retry（检查费用后 --resume；可灵可改 scene_plan.shots[].rework_mode，未写则 regenerate；这不是人审）")
        elif status == "await_episode":
            print("produce: await_episode（本集完成，--resume 下一集；这不是人审）")
        else:
            print("produce: ok")
        _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
        return 0
    if status == "over_hero":
        print(f"错误: {result.get('error') or 'pending hero 超过 30%'}（--trim-hero 或 --all-video）", file=sys.stderr)
        _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
        return int(result.get("code") or 2)
    print(f"错误: {result.get('error') or 'produce 失败'}", file=sys.stderr)
    _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
    return int(result.get("code") or 2)


def _cmd_webui(args: argparse.Namespace) -> int:
    try:
        from montage.webui.server import run
    except ImportError:
        print("缺少 WebUI 依赖：pip install \"montage-core[webui]\"", file=sys.stderr)
        return 2
    if str(args.host) not in ("127.0.0.1", "localhost", "::1"):
        print(f"警告: --host {args.host} 无鉴权，仅应在本机或可信网络使用。", file=sys.stderr)
    print(f"看板启动: http://{args.host}:{args.port}  (root={Path(args.root).resolve()})")
    run(Path(args.root), host=args.host, port=args.port)
    return 0


def _cmd_delivery_report(args: argparse.Namespace) -> int:
    from montage.engine.delivery_report import (
        build_project_delivery_report,
        format_vlm_state,
        format_subtitle_timeline,
        format_transition_junctions,
    )
    from montage.engine.subtitle_timeline import format_srt_audit
    from montage.engine.cut_points import (
        format_cut_point_projection,
        format_m5_parallel_audit,
        format_m6_parallel_audit,
    )
    from montage.engine.transition_contract import (
        format_transition_contract_projection,
    )

    try:
        report = build_project_delivery_report(
            args.project_dir,
            quality_mode=args.quality_mode,
        )
    except Exception as exc:  # noqa: BLE001 - CLI surfaces a compact operator error
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        material = report.get("material_contract") or {}
        quality = report.get("quality_gate") or {}
        trace = report.get("traceability") or {}
        process = report.get("process_status") or {}
        vlm = quality.get("vlm") or {}
        duration = report.get("duration_reconciliation") or {}
        print(
            f"delivery={report.get('status')} "
            f"process={process.get('status')} "
            f"material={material.get('actual_ai_video')}/{material.get('required_shots')} "
            f"quality={quality.get('mode')} "
            f"vlm={format_vlm_state(vlm)} "
            f"vlm_verified={vlm.get('verified')} "
            f"events_healthy={trace.get('generation_events_healthy')}"
        )
        if duration:
            print(f"transitions={format_transition_junctions(duration)}")
        subtitle = report.get("subtitle_timeline")
        if subtitle is not None:
            print(f"subtitle={format_subtitle_timeline(subtitle)}")
        srt_audit = report.get("subtitle_srt_audit")
        if srt_audit is not None:
            print(f"srt={format_srt_audit(srt_audit)}")
        cuts = report.get("cut_points")
        if cuts is not None:
            print(f"cut_points={format_cut_point_projection(cuts)}")
        m5_audit = report.get("m5_parallel_audit")
        if m5_audit is not None:
            print(f"m5_audit={format_m5_parallel_audit(m5_audit)}")
        m6_audit = report.get("m6_parallel_audit")
        if m6_audit is not None:
            print(f"m6_audit={format_m6_parallel_audit(m6_audit)}")
        transition_contracts = report.get("transition_contracts")
        if transition_contracts is not None:
            print(
                "transition_contracts="
                f"{format_transition_contract_projection(transition_contracts)}"
            )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_review_findings(args: argparse.Namespace) -> int:
    from montage.engine.review_findings import (
        build_review_findings,
        load_review_log,
        query_finding_id_index,
    )

    project_dir = Path(args.project_dir)
    try:
        rows, parse_errors = load_review_log(
            project_dir / "artifacts" / "review_log.jsonl"
        )
        projection = build_review_findings(rows)
        findings = query_finding_id_index(
            projection,
            finding_id=args.finding_id,
            status=args.status,
            severity=args.severity,
            target=args.target,
        )
    except Exception as exc:  # noqa: BLE001 - CLI surfaces a compact operator error
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    filters = {
        "finding_id": args.finding_id,
        "status": args.status,
        "severity": args.severity,
        "target": args.target,
    }
    if args.summary:
        print(
            f"log_present={rows is not None} "
            f"total={projection.get('finding_count', 0)} "
            f"matched={len(findings)} "
            f"occurrences={sum(int(row.get('occurrence_count') or 0) for row in findings)} "
            f"parse_errors={len(parse_errors)}"
        )
        return 0

    print(json.dumps({
        "schema_version": 1,
        "log_present": rows is not None,
        "filters": filters,
        "total_findings": projection.get("finding_count", 0),
        "matched_findings": len(findings),
        "matched_occurrences": sum(
            int(row.get("occurrence_count") or 0) for row in findings
        ),
        "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors,
        "findings": findings,
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_srt_rebuild(args: argparse.Namespace) -> int:
    from montage.engine.artifacts import ArtifactStore
    from montage.engine.subtitle_timeline import (
        audit_srt_sync,
        build_regenerated_srt,
    )

    root = Path(args.project_dir)
    store = ArtifactStore(root)
    compose_plan = store.read("compose_plan")
    if compose_plan is None:
        print("错误: compose_plan.json 缺失", file=sys.stderr)
        return 2
    progress = store.read("produce_progress") or {}
    finish = ((progress.get("steps") or {}).get("finish") or {}) if isinstance(progress, dict) else {}
    title_offset = float(finish.get("title_dur") or 0)
    film_health = store.read("film_health") or {}
    final_duration = (
        (film_health.get("probe") or {}).get("duration_seconds")
        if isinstance(film_health, dict) else None
    )
    output = Path(args.output) if args.output else root / "renders" / "final.srt"
    existing = output.read_text(encoding="utf-8") if output.is_file() else None

    try:
        rebuilt = build_regenerated_srt(
            compose_plan,
            title_offset_seconds=title_offset,
            max_chars_per_line=args.max_chars,
        )
    except (ValueError, TypeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    audit_before = audit_srt_sync(
        compose_plan=compose_plan,
        srt_text=existing,
        title_offset_seconds=title_offset,
        final_duration_seconds=final_duration,
    )
    audit_after = audit_srt_sync(
        compose_plan=compose_plan,
        srt_text=rebuilt["srt"],
        title_offset_seconds=title_offset,
        final_duration_seconds=final_duration,
    )

    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + ".tmp")
        try:
            tmp.write_text(rebuilt["srt"], encoding="utf-8")
            os.replace(tmp, output)
        except PermissionError:
            # Windows controlled folders may reject sibling .tmp creation.
            output.write_text(rebuilt["srt"], encoding="utf-8")
        status = "written"
    else:
        status = "dry_run"

    print(json.dumps({
        "status": status,
        "write": bool(args.write),
        "output_path": str(output),
        "title_offset_seconds": title_offset,
        "cue_count": rebuilt.get("cue_count"),
        "srt_sha256": hashlib.sha256(rebuilt["srt"].encode("utf-8")).hexdigest(),
        "srt_text": rebuilt["srt"],
        "audit_before": audit_before,
        "audit_after": audit_after,
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="montage", description="montage-core CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="环境自检")
    p_doctor.add_argument("--pipeline", default=None, help="打印该管线导演技能路径")
    p_doctor.add_argument("--project", default=None, help="按项目 pipeline_type 打印技能路径")
    p_doctor.add_argument("--json", dest="as_json", action="store_true", help="一行 JSON（不含密钥值）")
    sub.add_parser("tools", help="列出工具")

    p_init = sub.add_parser("init", help="初始化项目工作区")
    p_init.add_argument("project_id")
    p_init.add_argument("--title", default="untitled")
    p_init.add_argument("--pipeline", default="cinematic")
    p_init.add_argument("--root", default=str(Path.cwd()))

    p_status = sub.add_parser("status", help="查看阶段状态")
    p_status.add_argument("project_dir")

    p_check = sub.add_parser("check", help="写 checkpoint")
    p_check.add_argument("project_dir")
    p_check.add_argument("stage", choices=STAGE_ORDER)
    p_check.add_argument("--completed", action="store_true")
    p_check.add_argument("--approved", action="store_true", help="人类已审批（门禁阶段必填）")
    p_check.add_argument("--artifact", default=None)
    p_check.add_argument("--caps", default=None, help="本次使用的能力族列表（逗号分隔），越权则告警，如 analysis,prompt_engineering")
    p_check.add_argument("--caps-strict", dest="caps_strict", action="store_true", help="能力族越权时失败（默认仅告警）")

    p_run = sub.add_parser("run", help="执行已注册工具（--input JSON 文件）")
    p_run.add_argument("project_dir")
    p_run.add_argument("tool")
    p_run.add_argument("--input", dest="input_path", default=None, help="inputs.json（Windows 推荐）")
    p_run.add_argument("--json", dest="json_inline", default=None, help="内联 JSON（PowerShell 易踩坑，优先用 --input）")

    p_ae = sub.add_parser("auto_edit", help="AutoEditor 快速路径（不进 7 阶段管线）")
    p_ae.add_argument("project_dir", help="项目目录（缺 project.json 时在该目录原地写入，不调用 init）")
    src = p_ae.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="单个视频文件")
    src.add_argument("--clips", nargs="+", help="多段视频（顺序拼接，段内独立分析）")
    src.add_argument("--audio-only", dest="audio_only", help="仅音频（黑场占位 + 混入音轨）")
    p_ae.add_argument("--style", default="documentary", help="风格包 id（默认 documentary）")
    p_ae.add_argument("--profile", default=None, help="覆盖 output_profile（如 douyin_vertical）")
    p_ae.add_argument("--overrides", default=None, help="JSON 对象：StylePack 覆写和/或 replan（段/镜头）")
    p_ae.add_argument("--has-bgm", dest="has_bgm", action="store_true")
    p_ae.add_argument("--is-speech", dest="is_speech", action="store_true")
    p_ae.add_argument(
        "--bpm", type=float, default=0.0,
        help="P0-5：曲库/人给的 bpm。0（默认）= 对能量包络自相关估拍（estimated，必带 warning）",
    )
    p_ae.add_argument(
        "--beats-per-bar", dest="beats_per_bar", type=int, default=4,
        help="P0-5：每 bar 拍数（默认 4/4）",
    )
    p_ae.add_argument(
        "--no-beat-cuts", dest="no_beat_cuts", action="store_true",
        help="P0-5：有 --has-bgm 也不用能量波接管切点（回到纯 scene-change 切点）",
    )
    p_ae.add_argument(
        "--vfx", dest="vfx", default=None, metavar="JSON",
        help=(
            "P0-8：按镜写后期特效（JSON 对象 shot_id→vfx[]，或 JSON 文件路径）。"
            "例：'{\"seg_0_shot_0\":[{\"layer\":\"post\",\"kind\":\"impact_flash\"}]}' "
            "——onset 缺省时吸附 beat_map 能量峰；走 replan 写入 plan"
        ),
    )
    p_ae.add_argument("--language", default="zh")
    p_ae.add_argument("--target-duration", dest="target_duration", type=float, default=None)
    p_ae.add_argument("--preview", action="store_true", help="只出 480p 预览到 tmp_autoedit/preview.mp4")
    p_ae.add_argument("--no-resume", dest="no_resume", action="store_true", help="忽略中间产物，全量重渲")
    p_ae.add_argument(
        "--scene-index", dest="scene_index", default=None, metavar="PATH",
        help="P0-2 情节单元索引 JSON（默认自动读 <project>/artifacts/scene_index.json）",
    )
    p_ae.add_argument(
        "--no-scene-index", dest="no_scene_index", action="store_true",
        help="忽略 scene_index，纯视觉切点断镜",
    )
    p_ae.add_argument("--reason", default="", help="本版改动说明（写入 plan_history + decisions.jsonl）")

    p_prod = sub.add_parser("produce", help="已有分镜片段拼片，或 --idea 收编圣经")
    p_prod.add_argument("project_dir")
    p_prod.add_argument("--resume", action="store_true", help="只跑未成功的步骤")
    p_prod.add_argument("--skip-export", dest="skip_export", action="store_true")
    p_prod.add_argument("--strict-audio", dest="strict_audio", action="store_true", help="缺 BGM 则失败")
    p_prod.add_argument("--keep-scratch", dest="keep_scratch", action="store_true", help="保留中间文件")
    p_prod.add_argument(
        "--prune-exports", dest="prune_exports", type=int, default=0, metavar="N",
        help="导出后只保留最新 N 个 zip（默认 0=只增不删）",
    )
    p_prod.add_argument("--idea", default=None, help="W1：级联分析并收编圣经（不生成、不拼片）")
    p_prod.add_argument("--tts", action="store_true", help="W2：生成后合成对白（默认不合成）")
    p_prod.add_argument("--trim-hero", dest="trim_hero", action="store_true", help="W3：把超额 pending hero 降为 talk")
    p_prod.add_argument("--all-video", dest="all_video", action="store_true", help="W3：全镜 I2V，跳过 30%%（成片已齐则忽略）")
    p_prod.add_argument("--skip-finish", dest="skip_finish", action="store_true", help="跳过 LUT/profile/片头（release 仍跑）")
    p_prod.add_argument("--burn-subs", dest="burn_subs", action="store_true", help="finish 把字幕烧进像素（默认只写 SRT 旁路）")
    p_prod.add_argument("--profile", default="", help="finish 显式平台档案；不读管线 default_profile")
    p_prod.add_argument("--retry", default=None, help="重跑镜 id，逗号分隔；未 --yes/--resume 则 await_retry")
    p_prod.add_argument("--yes", action="store_true", help="确认 retry（只对此生效）")
    p_prod.add_argument(
        "--season-concat",
        dest="season_concat",
        action="store_true",
        help="系列根：全集完成后拼接 renders/season.mp4（默认不拼）",
    )
    p_prod.add_argument(
        "--accept-degraded-vlm",
        dest="accept_degraded_vlm",
        action="store_true",
        help=(
            "仅 degraded/manual_only 且 VLM 未验证时有效：显式记录 "
            "human_review.decision=accepted_with_degraded_vlm；不改变 delivery=degraded"
        ),
    )
    p_prod.add_argument(
        "--review",
        default="director",
        choices=["bible", "none", "each_episode", "director"],
        help="--idea 时 director 走八步停点；bible 轻量停；无 --idea 时 none 跳过样品；系列根 bible/each_episode 按集停。默认 director",
    )

    p_webui = sub.add_parser("webui", help="启动 Web 看板")
    p_webui.add_argument("--host", default="127.0.0.1")
    p_webui.add_argument("--port", type=int, default=8399)
    p_webui.add_argument("--root", default=str(Path.cwd()))
    p_delivery = sub.add_parser(
        "delivery_report",
        help="生成只读交付快照（默认输出 JSON，不写 artifact）",
    )
    p_delivery.add_argument("project_dir")
    p_delivery.add_argument(
        "--summary",
        action="store_true",
        help="仅输出一行关键状态",
    )
    p_delivery.add_argument(
        "--quality-mode",
        dest="quality_mode",
        choices=["full", "strict", "degraded", "manual_only"],
        default="degraded",
        help="质量策略语义（full/strict 阻断行为将在下一小步接入）",
    )
    p_srt = sub.add_parser(
        "srt_rebuild",
        help="按 xfade 投影重生成 SRT；默认 dry-run，--write 才写盘",
    )
    p_srt.add_argument("project_dir")
    p_srt.add_argument(
        "--write",
        action="store_true",
        help="显式写回 renders/final.srt；不加则只预览",
    )
    p_srt.add_argument(
        "--output",
        default=None,
        help="可选输出路径；默认 renders/final.srt",
    )
    p_srt.add_argument(
        "--max-chars",
        dest="max_chars",
        type=int,
        default=20,
        help="每行最大字符数（默认 20）",
    )
    p_findings = sub.add_parser(
        "review_findings",
        help="只读查询 review finding 生命周期（默认输出 JSON，不写 artifact）",
    )
    p_findings.add_argument("project_dir")
    p_findings.add_argument("--finding-id", dest="finding_id", default=None)
    p_findings.add_argument(
        "--status",
        dest="status",
        default=None,
        choices=["open", "in_progress", "fixed", "verified", "waived", "invalid"],
    )
    p_findings.add_argument("--severity", dest="severity", default=None)
    p_findings.add_argument("--target", dest="target", default=None)
    p_findings.add_argument(
        "--summary",
        action="store_true",
        help="仅输出一行总量、匹配数和日志健康状态",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from montage.engine.envfile import load_envfile

    load_envfile()
    args = build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return _cmd_doctor(_discover(), args)
    if args.cmd == "tools":
        return _cmd_tools(_discover())
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "auto_edit":
        return _cmd_auto_edit(args)
    if args.cmd == "produce":
        return _cmd_produce(args)
    if args.cmd == "delivery_report":
        return _cmd_delivery_report(args)
    if args.cmd == "srt_rebuild":
        return _cmd_srt_rebuild(args)
    if args.cmd == "review_findings":
        return _cmd_review_findings(args)
    if args.cmd == "webui":
        return _cmd_webui(args)
    return 1
