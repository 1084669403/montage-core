"""逐镜规约层 — 手工精修的结构化契约表（纯数据 + 查询，无依赖）。

从九条参考母带提炼，承载提示词构建器单靠 shot 结构表达不了的三类信息：
- 镜头类型强制表（``shot_type_contracts``）：每场每镜的强制景别/角度/时长。
- 一镜到底 beat 序列（``one_take_beats``）：单长镜内的固定走位顺序。
- 跨镜状态演进（``state_timeline``）：角色外观/状态随镜头切换的唯一开关。
- 角色弧线（``character_arc``）：角色跨段情绪/动作弧线。
- 卡司构成（``cast_contract``）：必现/禁现角色与标签唯一规则。
- 人群规则（``crowd_rules``）：群集密度/队形/穿模约束。
- 声画同步（``audio_sync``）：声音触发与画面动作的帧级对齐。
- 情绪能量曲线（``energy_curve``）：整片的情绪/节奏走向。

查询函数按 ``master_pattern`` + 场景/镜头 id 取对应合约；未知返回空表，
绝不抛异常。这些表由 ``lib.shot_prompt_builder.build_kling_prompt`` 消费。
"""

from __future__ import annotations

from typing import Any

# 模式 id → 镜头类型强制表。每场每镜一条：
#   scene_id: {shot_index (1 基): {type, mandatory, duration, note}}
# duration 一律为「整片时间窗」（如 0-3s / 3-6s），与 _kling_shot_timestamp 的
# [Ns-Ms] 前缀语义一致；不要写单镜时长（3s），否则时间戳会按 0 起算锚错起点。
SHOT_TYPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "space_dive": {
        "scenes": {
            "*": {
                1: {"type": "分层建立·强俯视+荷兰角", "mandatory": True, "duration": "0-3s", "note": "前景清晰飞船、极高空、三层空气透视"},
                2: {"type": "远景对峙·小飞船对昆虫大军", "mandatory": True, "duration": "3-6s", "note": "小飞船 vs 铺天盖地巨虫"},
                3: {"type": "座舱内 47mm 拉杆蓄力", "mandatory": True, "duration": "6-8s", "note": "俯拍、喷蓝色气焰"},
                4: {"type": "侧面中景冲撞", "mandatory": True, "duration": "8-10s", "note": "侧向撞碎甲虫、冲击波"},
                5: {"type": "被虫群包围·环绕镜头", "mandatory": True, "duration": "10-13s", "note": "飞船被团团包围、左冲右突"},
                6: {"type": "极致远景流星俯冲", "mandatory": True, "duration": "13-16s", "note": "宇宙黑幕留白、飞船如流星"},
                7: {"type": "第一视角 FPV 冲进花心", "mandatory": True, "duration": "16-17s", "note": "仅 1 秒主观镜头"},
                8: {"type": "座舱正面三人反应", "mandatory": True, "duration": "17-20s", "note": "狂欢高潮"},
            },
        },
        "default_duration": "3s",  # 仅备查，不参与时间戳计算
    },
    "underwater_rescue": {
        "scenes": {
            "*": {
                1: {"type": "广角上望随流漂移", "mandatory": True, "duration": "0-3s"},
                2: {"type": "中景随种子跟拍", "mandatory": True, "duration": "3-6s"},
                3: {"type": "越肩低角俯望风扇喉口", "mandatory": True, "duration": "6-9s"},
                4: {"type": "快速回甩水獭并肩追踪", "mandatory": True, "duration": "9-11s"},
                5: {"type": "短慢镜·风扇近旁回望", "mandatory": True, "duration": "11-13s", "note": "风扇—种子—水獭空间顺序"},
                6: {"type": "中景双人剧烈抖动", "mandatory": True, "duration": "13-15s"},
                7: {"type": "上摇·顶舱盖拍击", "mandatory": True, "duration": "15-17s"},
                8: {"type": "种子急速变焦特写", "mandatory": True, "duration": "17-20s", "note": "黑点↔黑✗首次切换"},
                9: {"type": "狂乱越肩追逐", "mandatory": True, "duration": "20-22s"},
                10: {"type": "昏厥种子紧特写", "mandatory": True, "duration": "22-24s"},
                11: {"type": "混乱追踪最大抖动", "mandatory": True, "duration": "24-27s"},
                12: {"type": "广角上摇留守管内", "mandatory": True, "duration": "27-30s", "note": "二者出画、镜头不跟出"},
            },
        },
        "default_duration": "2-3s",  # 仅备查，不参与时间戳计算
    },
    "celebration_carnival": {
        "scenes": {
            "*": {
                1: {"type": "种子背后 OTS 过山车滑梯", "mandatory": True, "duration": "0-5s", "note": "沿蕨叶俯冲滑下，必拍"},
                2: {"type": "大幅高速环绕平台", "mandatory": True, "duration": "5-10s"},
                3: {"type": "动态跟拍对话", "mandatory": True, "duration": "10-14s", "note": "貘老板问+水獭答"},
                4: {"type": "孢子烟花爆发", "mandatory": True, "duration": "14-19s", "note": "台词话音未落时炸响"},
                5: {"type": "貘老板入舞", "mandatory": True, "duration": "19-24s"},
                6: {"type": "全体上升·接近发光出口", "mandatory": True, "duration": "24-30s"},
            },
        },
        "default_duration": "5s",  # 仅备查，不参与时间戳计算
    },
    "vertical_fall": {
        "scenes": {
            "*": {
                1: {"type": "破窗一镜到底", "mandatory": True, "duration": "0-11s", "note": "与 ONE_TAKE_BEATS.first_shot 一致；首秒即破窗"},
                2: {"type": "对话转焦", "mandatory": True, "duration": "11-15s", "note": "貘老板特写首切"},
                3: {"type": "巨兽级巨虫破窗", "mandatory": True, "duration": "15-19s", "note": "强破碎、强巨物"},
                4: {"type": "拉远揭示", "mandatory": True, "duration": "19-23s", "note": "主体面朝镜头、巨虫追来"},
                5: {"type": "鸟瞰灾难全景", "mandatory": True, "duration": "23-27s", "note": "微荷兰角、大厦被摧毁+蜈蚣缠楼"},
                6: {"type": "回头推镜", "mandatory": True, "duration": "27-30s", "note": "水獭惊恐特写、硬切黑场"},
            },
        },
        "default_duration": "4s",  # 仅备查，不参与时间戳计算
    },
}

# 模式 id → 一镜到底 beat 序列
#   first_shot: 单长镜的时间窗（格式与契约表 duration 一致，如 0-11s）；beats: 固定走位顺序；cut_at: 首次切镜点
ONE_TAKE_BEATS: dict[str, dict[str, Any]] = {
    "vertical_fall": {
        "first_shot": "0-11s",
        "beats": [
            "首秒即破窗、水獭种子并排炸窗而出",
            "破窗逆光光晕、冷白刺眼阳光灌入、碎玻璃悬浮飞溅（极短升格）",
            "落至垂直幕墙、并肩沿玻璃向下飞奔、种子小短腿打滑",
            "转至水獭正面中景、水獭转头看身后",
            "巨大蓝色天牛自二者下方玻璃破出、前景回头+中景巨躯几乎覆盖画面",
            "二者转回头向下狂奔、侧面跟拍、玻璃飞溅天牛紧逼",
            "石油蓝飞船自后上方缓缓飞入并行、貘老板从容呷咖啡",
            "水獭狂奔中难以置信回头看向老板（恐慌 vs 从容反差）",
        ],
        "cut_at": "镜头 2 老板特写首次切换",
    },
}

# 模式 id → 跨镜状态演进
#   character: {state_a, state_b, switch_shot, never_before_shot, note}
STATE_TIMELINE: dict[str, dict[str, Any]] = {
    "underwater_rescue": {
        "seed": {
            "character": "种子",
            "state_a": "纯黑圆点眼（默认）",
            "state_b": "纯黑 ✗ 叉号眼（濒死昏厥）",
            "switch_shot": 8,
            "never_before_shot": 8,
            "note": "镜头 1–7 全为黑点，黑✗ 仅镜头 8 起出现，此前零次",
        },
    },
}

# 模式 id → 角色弧线（跨段）
#   character: {stages: [...], switch_shot: N}
CHARACTER_ARCS: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "貘老板": {
            "stages": ["笨拙抗拒、双手推拒不想跳", "被种子们拉着卷进去", "放开大笑同跳、乐在其中"],
            "switch_shot": 5,
        },
    },
}

# 模式 id → 卡司构成与标签唯一
#   required: 必现；forbidden: 禁现；label_uniqueness: 标签唯一规则
CAST_CONTRACTS: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "required": ["水獭（戴蓝帽蓝夹克）", "主角种子（带条形码）", "普通种子群（无码）", "飞船", "貘老板"],
        "forbidden": ["复制主角", "镜像重影", "多余无名角色", "任何条形码/贴纸出现在普通种子上"],
        "label_uniqueness": {
            "unique": "主角种子（图片2）",
            "label": "条形码",
            "rule": "条形码/贴纸仅主角种子独有，普通种子群一律不带",
        },
    },
    "underwater_rescue": {
        "required": ["水獭", "种子"],
        "forbidden": ["第二只种子", "第二只水獭", "更换道具", "拍摄设备"],
        "label_uniqueness": {
            "unique": "种子",
            "label": "壳上条形码",
            "rule": "仅一颗种子、条形码始终在壳上",
        },
    },
}

# 模式 id → 人群/群集规则
CROWD_RULES: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "density": "3x",
        "no_grid": True,
        "pose_variety": True,
        "no_interpenetration": True,
        "spacing": "零散随机、高低错落",
        "note": "舞姿各异、绝不排整齐队形/均匀网格",
    },
    "vertical_fall": {
        "density": "巨兽级",
        "no_grid": True,
        "pose_variety": True,
        "no_interpenetration": True,
        "spacing": "形态各异、不同物种混杂",
        "note": "严禁克隆同款整齐排列，严禁缩为蚊蝇/小黑点",
    },
}

# 模式 id → 声画同步触发
#   trigger: 声音/台词；action: 画面动作；timing: 对齐要求
AUDIO_SYNCS: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "trigger": "水獭喊出「This is Seedance!!」话音未落的一刹那",
        "action": "所有蕨类花蕊骤然收缩蓄力、像礼花炮同时喷射漫天发光孢子、爆发强逆光",
        "timing": "须在水獭台词话音未落的一刹那炸响",
    },
}

# 模式 id → 情绪/节奏能量曲线
ENERGY_CURVES: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "emotion": "畅快失重尖叫欢笑",
        "start": "0-5s 种子 OTS 滑梯沉浸开场",
        "build": "5-14s 环绕狂欢、对话",
        "peak": "14-19s 孢子烟花爆发（话音未落）",
        "after_peak": "19-24s 貘老板入舞、狂欢顶点",
        "end": "24-30s 全体上升升向发光出口、极致庆典高潮",
        "note": "越到后段越 high",
    },
}

# 模式 id → 色域铁律（allowed/forbidden casts，替代 hard_constraints 字符串）
PALETTE_LAWS: dict[str, dict[str, Any]] = {
    "vertical_fall": {
        "allowed": ["镜面蓝灰 60%", "主体海军蓝 30%", "暖橙点缀 10%", "冷白冷蓝高光/眩光"],
        "forbidden": ["暖金橙", "黄昏落日", "金色时刻", "橙粉天空", "暖黄环境光"],
        "note": "高光/眩光/镜头光晕一律冷白冷蓝",
    },
    "space_dive": {
        "allowed": ["高空白/浅灰天空 60%", "宇宙深黑+发光蓝边界线 30%", "蓝焰/橙红火光/朱红 10%"],
        "forbidden": ["廉价蓝滤镜", "四平八稳正视", "暖金橙主导"],
        "note": "冷调为主，暖色仅限摩擦火光与花朱红",
    },
    "celebration_carnival": {
        "allowed": ["粉橙/暖玫瑰/嫩黄 60%", "品红/紫罗兰/深红 30%", "深棕种子/深蓝船身/暖金光 10%"],
        "forbidden": ["廉价蓝滤镜", "低饱和灰暗"],
        "note": "饱和柔美、高对比却明亮温暖喜庆",
    },
}

# 模式 id → 尺度铁律
SCALE_IRON_LAWS: dict[str, dict[str, Any]] = {
    "space_dive": {
        "rule": "飞船渺小如小白点；每只昆虫至少四倍大；花行星级",
        "forbidden": ["小黑点", "碎点", "蚁群", "花粉状颗粒"],
    },
    "vertical_fall": {
        "rule": "所有巨虫怪兽级、两层楼高以上，无论爬墙或飞行",
        "forbidden": ["蚊蝇", "小黑点", "克隆同款"],
    },
    "celebration_carnival": {
        "rule": "种子群约常规三倍、成潮铺满画面",
        "forbidden": ["稀疏零落", "整齐队列"],
    },
}

# 模式 id → 方向铁律
FLOW_DIRECTION_LAWS: dict[str, dict[str, Any]] = {
    "vertical_fall": {
        "rule": "主体沿垂直幕墙向下狂奔",
        "forbidden": ["向上攀爬观感"],
    },
    "chase_axis": {
        "rule": "屏幕方向恒左→右推进，猎物右前、追赶者左后",
        "forbidden": ["越轴", "并排奔跑"],
    },
}

# 模式 id → 辉光/泛光铁律
GLOW_LAWS: dict[str, dict[str, Any]] = {
    "celebration_carnival": {
        "rule": "辉光/泛光/各向异性水平光丝/逆光耀斑/halation 为风格签名",
        "forbidden": ["廉价发光", "塑料感"],
        "note": "自然曝光、明亮通透",
    },
    "film_spectacle": {
        "rule": "克制的电影冷蓝，白平衡偏冷",
        "forbidden": ["浓艳的一片蓝", "暖金橙"],
    },
}


def _contract(mode_id: str | None, table: dict[str, Any]) -> dict[str, Any] | None:
    if not mode_id:
        return None
    return table.get(str(mode_id).strip())


def shot_type_contract(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, SHOT_TYPE_CONTRACTS)


def one_take_beats(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, ONE_TAKE_BEATS)


def state_timeline(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, STATE_TIMELINE)


def character_arcs(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, CHARACTER_ARCS)


def cast_contract(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, CAST_CONTRACTS)


def crowd_rules(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, CROWD_RULES)


def audio_syncs(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, AUDIO_SYNCS)


def energy_curves(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, ENERGY_CURVES)


def palette_law(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, PALETTE_LAWS)


def scale_iron_law(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, SCALE_IRON_LAWS)


def flow_direction_law(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, FLOW_DIRECTION_LAWS)


def glow_law(mode_id: str | None) -> dict[str, Any] | None:
    return _contract(mode_id, GLOW_LAWS)


# 该镜的类型强制（按镜头序号 1 基；scene_id 可用 "*" 通配）
def shot_type_for(
    mode_id: str | None,
    shot_index: int,
    scene_id: str = "*",
) -> dict[str, Any] | None:
    table = shot_type_contract(mode_id)
    if not table:
        return None
    scenes = table.get("scenes") or {}
    row = None
    if scene_id and str(scene_id) != "*":
        row = (scenes.get(str(scene_id)) or {}).get(int(shot_index))
    if row is None:
        row = (scenes.get("*") or {}).get(int(shot_index))
    return row if isinstance(row, dict) else None
