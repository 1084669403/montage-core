"""可灵 Omni 母带模式库（纯数据，无依赖）。

从九条高质量中文参考母带提炼出的七种命名模式。每种模式是结构化的
「全局母带句 + 硬约束槽 + 音频原则」，导演/playbook 按 ``mode_id`` 选用，
不用每次手写母带。

本模块只含数据与 ``pick_master`` 查询，不含 prompt 拼装逻辑；
拼装逻辑在 lib.shot_prompt_builder.build_kling_prompt。
"""

from __future__ import annotations

from typing import Any

# 模式 id → 母带定义。字段语义对齐 playbook 的 visual_language / asset_generation，
# 不新发明 schema。
KLING_MASTER_MODES: dict[str, dict[str, Any]] = {
    "film_spectacle": {
        "id": "film_spectacle",
        "title": "电影感奇观·真实质感",
        "master_prompt": (
            "照片级真人实拍 VFX 质感，实体电影镜头，IMAX / 21:9 变形宽银幕，"
            "180° 快门运动模糊，细腻胶片颗粒，自然白昼高调光，通透不压暗，"
            "色调 60:30:10 比例，全程硬切多镜头，实时速度，无字幕无 logo"
        ),
        "hard_constraints": [
            "全程大白天，不夜景不暗景",
            "主体只可远远位于背景，不贴镜头不占前景",
            "不可见细节黑名单：花心/内部仅指定镜头可见，其余一律禁止",
            "无任何可辨认地标/名建筑/国别天际线/招牌/旗帜",
            "逃命人群只能远远匿名，不近景人物",
            "真实比例超大主体只是离得远，不可缩小成小物",
        ],
        "audio_principle": "无音乐无对白，只保留现场环境音",
    },
    "chase_axis": {
        "id": "chase_axis",
        "title": "单向追逐轴·喜剧物理",
        "master_prompt": (
            "照片级真人实拍，21:9 变形宽银幕，35mm 胶片颗粒，180° 快门运动模糊，"
            "真实电影布光，冷调高对比室内光，60:30:10 色彩比例，全程硬切，"
            "真实重力/质量/惯性，原创角色无 logo 无真实品牌"
        ),
        "hard_constraints": [
            "三次抓捕一次比一次接近：半米→擦及→几乎按住",
            "每次甩回物件恰在将抓到之际落下、打断抓捕",
            "仅允许唯一一处慢动作（第二镜刀擦脸瞬间），撞击升回全速，其余实时",
            "甩出物件按真实弧线/质量/惯性飞行旋转",
            "单一场景不换地点；无动画/3D/游戏/赛璐璐/漫画特效/拟声字形；无字幕无水印",
            "喜剧基调，无血腥",
        ],
        "audio_principle": "无对白无人声；无音乐或极低喜剧配乐；只留现场音效",
    },
    "signature_orbit": {
        "id": "signature_orbit",
        "title": "招牌镜头运动·360° 环绕",
        "master_prompt": (
            "真人实拍包裹中间一段超高帧率升格慢动作的完整 360° 环绕，"
            "单镜到底，21:9 横屏，超写实真人实拍，浅景深，自然运动模糊"
        ),
        "hard_constraints": [
            "核心不可妥协：相机绕两角色转足完整一整圈 360°，匀速不间断，回到起点侧面机位，"
            "非半圈、非 90°/180° 弧线、非来回小幅摆动；四象限正面→远侧→背面→近侧→正面",
            "升格慢动作非冻结帧：两角色极慢流畅运动、相机持续运动不静止",
            "绕两角色整体为圆心，每度均须同框，不单独绕其一",
            "标志造型强制：蓝帽蓝夹克每帧每画风清晰正确，永不省略永不改色",
            "风格跳变：每种媒介整屏重绘两角色与店铺，非滤镜叠加、非只换背景",
            "地点恒为同一间被砸毁的快餐店，画风仅为技法/笔触，绝不更换地点",
            "种子身份一致：裂壳、绿芽披风、条形码贴纸",
        ],
        "audio_principle": "默片式，仅画面内音效；无音乐无对白无人声",
    },
    "underwater_rescue": {
        "id": "underwater_rescue",
        "title": "水下管道救援",
        "master_prompt": (
            "30 秒 21:9 横屏水下短片，8K 完全照片级真人水下质感，实体电影镜头，"
            "真实水体/焦散/浑浊，重胶片颗粒，180° 快门运动模糊，变形宽银幕，"
            "全程水下颗粒与微气泡飘过，高对比自然主义水下光，冷钢蓝灌满管道，"
            "亮而不昏暗，快速硬切多镜头，绝不出水绝不离下水道"
        ),
        "hard_constraints": [
            "空间顺序：风扇—种子—水獭；水獭自画面前方俯冲夺回，而非已抱住它",
            "环境须始终为复杂迷宫，无空白无特征表面",
            "末镜须留守管内，二者出画后镜头不跟出",
            "风扇巨大敞开无网、涡流致命；种子最后一瞬才被抓住、几近绞碎",
            "种子无眼白，眼睛仅两种形态：黑点默认，黑✗仅镜头 8 濒死起出现，此前零次",
            "两角色原创、跨镜一致不漂移，仅一颗种子一只水獭，不更换道具",
            "色调冷调高对比、明亮不昏暗；无 logo/水印/字幕/画面文字",
        ],
        "audio_principle": "无音乐无对白，只留闷响水下环境音与管道共鸣、风扇轰鸣、切水声、金属铛响",
    },
    "vertical_fall": {
        "id": "vertical_fall",
        "title": "垂直幕墙下坠·伪纪录片",
        "master_prompt": (
            "照片级真人电影实拍质感，超写实毛发，35mm 胶片颗粒，冷调通透大片感，"
            "强手持伪纪录片风格，广角变形宽镜 24-35mm，180° 快门动态模糊，"
            "正午冷调日光 6000-6500K，冷蓝晴空，强烈逆光与过曝但一律冷白冷蓝"
        ),
        "hard_constraints": [
            "水獭全片恒戴海军蓝棒球帽，破窗/狂奔/大风/回头均戴稳不脱落",
            "水獭空手奔跑，不抱不背种子；种子自行迈腿跟跑；二者恒同框相伴",
            "镜头 1 首秒即破窗，无静止空镜无前置铺垫",
            "镜头 5 为高空鸟瞰俯视、微荷兰角灾难全景，须展现整栋大厦被摧毁 + 超巨型蜈蚣缠楼",
            "无 logo/水印/字幕/屏幕文字",
        ],
        "audio_principle": "强手持现场声：呼啸风、玻璃碎裂、巨虫嘶鸣、旋翼/引擎，无音乐无对白",
    },
    "space_dive": {
        "id": "space_dive",
        "title": "高空飞船俯冲",
        "master_prompt": (
            "真实电影感动画短片，写实纪录片/实拍质感，胶片颗粒，浅景深，自然动态模糊，"
            "21:9 宽银幕，外景 24mm 广角、座舱内 47mm，高速追焦，180° 快门运动模糊，"
            "变形宽镜边缘畸变，强烈空气透视，极高垂直纵深，荷兰角+俯仰"
        ),
        "hard_constraints": [
            "严禁四平八稳正视/水平居中镜头，一律荷兰角倾斜加俯视或仰视",
            "空气透视三层：最上层飞船最清晰、中层巨虫略蓝灰雾、最下层巨花最朦胧发蓝",
            "花内部为黑暗深不见底、完全看不到花蕊的纯黑无底深渊",
            "俯冲有空气摩擦橙红火光与灼热气流的航空冲刺感",
            "无 logo/水印/字幕；台词仅英文并带情绪",
        ],
        "audio_principle": "引擎点火轰鸣、高速俯冲呼啸风切、撞碎甲壳爆裂、灼烧嘶鸣、冲进花心闷入低沉空旷回响、高空气流风声",
    },
    "celebration_carnival": {
        "id": "celebration_carnival",
        "title": "花蕊空中乐园·过山车庆典",
        "master_prompt": (
            "30 秒 21:9 宽银幕，8K 电影级超写实，35mm 胶片质感，自然颗粒，"
            "ARRI Alexa 美学，浅景深大师摄影，专业调色，治愈系奇幻绘本气质，"
            "柔和半透明、梦幻极致欢乐，过山车式极高能量运镜，辉光/泛光/各向异性"
            "水平光丝/逆光耀斑为风格签名，明亮通透自然曝光"
        ),
        "hard_constraints": [
            "情绪是核心：畅快失重尖叫欢笑贯穿、越到后段越 high、结尾升向光明的庆典高潮",
            "开场种子背后 OTS 视角、沿高空巨型蕨叶像滑梯/过山车轨道俯冲滑下为必拍",
            "全程超写实、自然、带胶片颗粒；无字幕/文字叠加/水印/logo；不复制主角/不镜像重影/不穿帮",
            "英文对白、口型自然同步；原创非 IP",
        ],
        "audio_principle": "环境音+台词、无背景音乐（或极轻快欢庆底噪）：气流呼啸、滑梯滑溜声、尖叫欢呼、荡秋千绷弦、孢子烟花绽放、升腾氛围嗡鸣",
    },
}

# 参考母带映射：哪条参考母带对应哪个模式（供文档/调试引用）。
MASTER_SOURCE_REFERENCES: dict[str, str] = {
    "film_spectacle": "参考 1 + 2",
    "chase_axis": "参考 4",
    "signature_orbit": "参考 5",
    "underwater_rescue": "参考 6 + 7",
    "vertical_fall": "参考 8",
    "space_dive": "参考 9",
    "celebration_carnival": "参考 10",
}

# 能力边界与返工映射：哪些母带指令可灵单次生成可能做不到，该用什么返工手段。
# 诚实标注——prompt 可强约束，但生成器不保证帧级/几何级精确。
CAPABILITY_GUARDRAILS: dict[str, list[dict[str, str]]] = {
    "film_spectacle": [
        {"instruction": "不可见细节黑名单（花心/内部仅指定镜可见）", "risk": "多镜硬切下逐帧保真有限", "mitigation": "--retry + --review 校验 + vlm_reviewer 把关；关键镜可用 I2V 首帧固定"},
        {"instruction": "超大主体只是离得远", "risk": "生成器可能缩小主体成小物", "mitigation": "母带写清真实比例；--retry"},
    ],
    "chase_axis": [
        {"instruction": "唯一一处慢动作（刀擦脸）", "risk": "prompt 不保证变速", "mitigation": "母带显式声明；真实变速留给 compose（ffmpeg speed/retake_segment）"},
        {"instruction": "三次抓捕一次比一次接近", "risk": "多镜硬切下升级节奏可能漂移", "mitigation": "逐镜写清距离；--retry 校验"},
    ],
    "signature_orbit": [
        {"instruction": "完整 360° 整圈回到起点", "risk": "生成器可能只给弧线/摆动", "mitigation": "最高优先指令 + --retry；仍不满意可 rework_mode=feature 重编或拆段"},
        {"instruction": "19 种风格二帧跳变", "risk": "单次逐帧保真有限", "mitigation": "默认单条指令；项目级可拆「风格跳变段」独立镜再 feature 拼接"},
        {"instruction": "超升格慢动作（非冻结帧）", "risk": "可能被渲染成冻结帧", "mitigation": "写清持续流动慢动作；compose 变速兜底"},
    ],
    "underwater_rescue": [
        {"instruction": "种子眼睛黑点→黑✗仅镜头8起", "risk": "多镜硬切下状态演进可能漂移", "mitigation": "状态演进写进逐镜块 + --retry 校验；镜头 8 濒死可用 I2V 首帧固定"},
        {"instruction": "风扇—种子—水獭空间顺序", "risk": "救援段空间关系可能错位", "mitigation": "母带写清空间顺序；--retry"},
    ],
    "vertical_fall": [
        {"instruction": "主体沿垂直幕墙向下狂奔", "risk": "可能被渲染成向上攀爬", "mitigation": "方向铁律最高优先 + --retry + vlm_reviewer"},
        {"instruction": "严禁暖金橙/冷白过曝", "risk": "色域可能漂移", "mitigation": "--retry + --review + vlm_reviewer 把关"},
        {"instruction": "镜头1 一镜到底 0-11s 固定走位", "risk": "单次长镜走位可能不连贯", "mitigation": "one_take_beats 序列 + --retry；必要时拆段"},
    ],
    "space_dive": [
        {"instruction": "镜头7 仅 1 秒 FPV 冲进花心", "risk": "单镜时长贴 Omni 下限 3s，1s 主观靠 prompt+硬切", "mitigation": "不承诺精确 1s；需要可拆段"},
        {"instruction": "飞船渺小如小白点 / 巨虫四倍大", "risk": "尺度可能漂移成小黑点", "mitigation": "尺度铁律 + --retry + vlm_reviewer"},
        {"instruction": "花心纯黑无底深渊", "risk": "可能被渲染出花蕊", "mitigation": "黑名单最高优先 + --retry"},
    ],
    "celebration_carnival": [
        {"instruction": "孢子烟花在台词话音未落时炸响", "risk": "prompt 不保证帧级声画对齐", "mitigation": "母带写清时序 + --retry / feature 重编"},
        {"instruction": "成潮种子群无克隆", "risk": "生成器易批量克隆", "mitigation": "提示词写舞姿各异/站位零散 + vlm_reviewer；必要时拆前景/背景层合成"},
        {"instruction": "OTS 开场必拍", "risk": "可能被其他运镜替代", "mitigation": "镜头1 类型强制最高优先"},
    ],
}


def pick_master(mode_id: str | None) -> dict[str, Any] | None:
    """按 mode_id 取母带定义；未知或缺失返回 None。"""
    if not mode_id:
        return None
    return KLING_MASTER_MODES.get(str(mode_id).strip())
