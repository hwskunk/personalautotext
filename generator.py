# ==========================================
# generator.py — 文案生成（两种类型要求 × 两种风格模式，流式输出）
# 生成时 = 风格模式(画像/示例组合) + 类型要求
# ==========================================
import random
import re

from config import ALLOWED_EXTENSIONS, DATA_DIR
from llm import get_chat_model
from style_manager import SUBTYPE_NAMES, _split_entries, load_style

# 开场场景池：每次生成由代码随机抽一个注入 prompt，
# 避免模型凭先验反复选同一个开场（如"送砖+摸砖面"）。
# 场景池只写"我做了什么"，不写"别人怎么评价"（客户满意/师傅夸等），
# 否则模型会顺着评价写营销腔，参考文本的感悟主体永远是"我"。
OPENING_SCENES = [
    "给客户送砖上门，卸货装车忙了一上午",
    "上门给客户量方，顺便看工地",
    "深夜收工，一个人清点今天的单子",
    "清晨开店，打扫展厅迎接顾客",
    "老客户介绍了个新客户过来",
    "在工地盯了一天的铺贴，腰都直不起来",
    "在展厅泡茶接待犹豫的客户",
    "陪客户蹲在地上挑花色，反复对比",
    "客户对价格有疑虑，耐心解释了一下午",
    "新品到货，拆箱验货",
    "客户家的砖铺完了，回访看看铺贴效果",
    "从仓库拉货装车，忙到天黑",
]
_scene_pool: list[str] = []


def _pick_scene() -> str:
    """洗牌后轮换取开场场景：一轮内绝不重复，取完再洗牌"""
    global _scene_pool
    if not _scene_pool:
        _scene_pool = OPENING_SCENES[:]
        random.shuffle(_scene_pool)
    return _scene_pool.pop()

# 每种文案类型的生成要求（追加到提示词尾部）
TYPES = {
    "xiaohongshu": {
        "label": "小红书种草文案",
        "requirement": (
            "请写一篇小红书种草风格的瓷砖文案，要求：\n"
            "1. 先给一个抓人的标题（2行以内，带emoji，不用#号）\n"
            "2. 正文：用场景/情感引出 → 自然带出对砖的真实感受 → 生活化体验 → 真诚推荐结尾\n"
            "3. 正文适当使用emoji、网络热词，段落短小\n"
            "4. 结尾换行给出 3-5 个话题标签（#开头）\n"
            "5. 全文 200-400 字\n"
            "6. 像博主分享真实生活，只提泛称，不编造型号、花色名、参数"
        ),
    },
    "wechat_moment": {
        "label": "朋友圈短文案",
        "requirement": (
            "请写一条朋友圈文案，像这位老板平时随手发的那样。参考文本是'格言体'：\n"
            "1. 主体是观念断言，主题面要宽：写信任、写坚持、写品质、写时节节气、写节日祝福、写晚归自嘲都可以，像参考样本那样换着角度写，这次写了信任，下次就换坚持或时节，不要每次都是同一个主题；感受示例原句的口吻（如'信任这东西很珍贵，一旦给了，就不能辜负''好的生意，从来不是靠低价内卷''在平凡的日常里坚守热爱'），主题要自己重新想\n"
            "2. 当天的状态只做引子，最多一两句带过，不要写事件流水账（不要'茶水烧了几遍''摸了几块砖''数到第几张'这类过程细节）\n"
            "3. 判断句是主体：'不是……，而是……''……，才是……''……，是……，也是……'这类都可以用，但句式要多样，和参考文本一样偶尔出现即可，不要每篇都重复同一个句式\n"
            "4. 收尾方式要多样：一句感谢、一句承诺、一句自我打气、一句自嘲或直接以表情收尾都可以，不要每篇都用'搬砖人''加油'这类固定收尾（参考文本里'加油，搬砖人'只偶尔出现）\n"
            "5. 可以对'你'（客户）说一句承诺或感谢，但必须落在信任、责任、靠谱上，不要写成促销表白（不要'你选的砖，我送的稳'这类对仗表白）\n"
            "6. 不编造人名，不出现对话和台词（'师傅说''客户说'都算），提到人只用泛称：客户、师傅、兄弟们、新老顾客\n"
            "7. 不写产品介绍，需要带出砖或服务时只用泛称，不编造产品名、型号、花色\n"
            "8. 不要排比堆砌、不要感叹号、不要话题标签，80-130 字"
        ),
    },
    "sales_script": {
        "label": "销售话术",
        "requirement": (
            "请写一段面对咨询客户的瓷砖销售话术，要求：\n"
            "1. 包含：开场破冰 → 需求挖掘 → 产品介绍 → 异议处理（质量/价格/花色）→ 促单引导\n"
            "2. 语气专业、真诚、有分寸，像资深门店销售\n"
            "3. 分段呈现，每段用【场景】小标题标注\n"
            "4. 全文 300-500 字，口语化\n"
            "5. 产品介绍用泛称（如“我们店里这款砖”“这几款热销的”），不编造系列名、花色名或具体参数，突出真诚实在"
        ),
    },
}

# 示例条目：单条最大字符数 / 每次生成携带的条目数
MAX_ENTRY_CHARS = 600
ENTRY_COUNT = 6

_example_pool: list[str] = []


def _refill_pool() -> None:
    """重建示例条目池：读取全部样本 → 切分条目 → 洗牌"""
    global _example_pool
    _example_pool = []
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
                _example_pool.extend(_split_entries(p.read_text(encoding="utf-8", errors="ignore")))
    random.shuffle(_example_pool)


# 子类型均衡抽取用的分类型池：每类一个小池，类内一轮不重复
_subtype_pools: dict[str, list[str]] = {}


def _pick_by_subtype(subtype: dict, n: int) -> str:
    """按子类型均衡抽取：每类按条数分配额，相邻两条不同类，类内一轮不重复
    subtype: {"状态型": [原句...], "观念型": [...], ...}"""
    global _subtype_pools
    classes = [c for c in SUBTYPE_NAMES if subtype.get(c)]
    if not classes:
        return ""
    counts = {c: len(subtype[c]) for c in classes}
    # 配额：先均分，余数给条目多的类（保证每类都有份）
    per, extra = divmod(n, len(classes))
    quota = {c: per for c in classes}
    for c in sorted(classes, key=lambda c: counts[c], reverse=True)[:extra]:
        quota[c] += 1
    # 每类池续用或初始化
    for c in classes:
        pool = _subtype_pools.get(c)
        if not pool:
            _subtype_pools[c] = subtype[c][:]
    picked, last = [], None
    while sum(quota.values()) > 0:
        # 优先选一个还有配额且与上一条不同类的；无解时放宽
        cands = [c for c in classes if quota[c] > 0 and c != last]
        if not cands:
            cands = [c for c in classes if quota[c] > 0]
        if not cands:
            break
        c = random.choice(cands)
        pool = _subtype_pools[c]
        if not pool:
            _subtype_pools[c] = subtype[c][:]  # 类内一轮取完，重建
            pool = _subtype_pools[c]
        picked.append(pool.pop())
        quota[c] -= 1
        last = c
    return "\n\n".join(f"【示例】\n{e[:MAX_ENTRY_CHARS]}" for e in picked)


# 信任类主题指纹：样本里信任/托付类条目扎堆（用户发文习惯），
# 示例若全抽到信任类，生成主题就会被带窄（总是"信任/靠谱"）。
# 抽取后做浓度检查，超过一半命中就重抽，让示例主题摊开。
TRUST_HINTS = ("信任", "托付", "辜负", "靠谱", "诚信", "守底线", "守护", "口碑", "真诚", "真心", "承诺", "放心")


def _trust_ratio(text: str) -> float:
    """粗略计算示例文本中信任类主题的占比（按条算，不按字数）"""
    blocks = [b for b in re.split(r"\n\n【示例】", text) if b.strip()]
    if not blocks:
        return 0.0
    hits = sum(1 for b in blocks if any(w in b for w in TRUST_HINTS))
    return hits / len(blocks)


def _pick_examples(n: int = ENTRY_COUNT, style: dict | None = None) -> str:
    """抽取示例：画像带 subtype 时按类均衡抽；否则全池洗牌轮换（池空自动重建，一轮内不重复）
    主题浓度检查：信任类占比 > 一半则重抽（最多 5 轮），避免生成主题被示例带窄"""
    picked = ""
    for _ in range(5):
        if style and style.get("subtype"):
            picked = _pick_by_subtype(style["subtype"], n)
            if not picked:
                return ""
        else:
            global _example_pool
            if not _example_pool:
                _refill_pool()
            picked = []
            while len(picked) < n and _example_pool:
                picked.append(_example_pool.pop())
            if not _example_pool:
                _refill_pool()  # 预填充下一轮
            if not picked:
                return ""
            picked = "\n\n".join(f"【示例】\n{e[:MAX_ENTRY_CHARS]}" for e in picked)
        if _trust_ratio(picked) <= 0.5:
            return picked
    return picked  # 重试多轮仍超标（池子信任密度过高），退回最后一轮


def _format_style(style: dict) -> str:
    """把新画像（可执行数据）格式化为提示词文本"""
    lines = []
    structure = style.get("structure")
    if structure and structure != "（未提炼）":
        lines.append(f"结构骨架：{structure}")
    ex = style.get("structure_example")
    if ex and ex != "（未提炼）":
        lines.append(f"（原句例：{ex}）")

    def fmt_templates(items, label):
        items = items or []
        if not items:
            return
        lines.append(f"{label}（参考库，按示例的多样性和频率取用，不要每篇套同一个模板）：")
        # 低频句式（"不是…而是…"类）排到段尾，降低首要曝光
        def is_low(it):
            p = it.get("pattern", "") if isinstance(it, dict) else ""
            return "不是" in p and "而是" in p
        for it in sorted(items, key=is_low):
            if not isinstance(it, dict):
                continue
            p, e = it.get("pattern", ""), it.get("example", "")
            if p and e:
                low = "（样本中低频，偶尔用）" if is_low(it) else ""
                lines.append(f"- {p} {low}例：{e}")
            elif p or e:
                lines.append(f"- {p or e}")

    fmt_templates(style.get("openings"), "开头句库")
    fmt_templates(style.get("core_templates"), "观念主体句库")
    fmt_templates(style.get("closings"), "收尾句库")

    imagery = style.get("imagery") or []
    if imagery:
        lines.append("意象：")
        for it in imagery:
            if isinstance(it, dict):
                d, e = it.get("domain", ""), it.get("examples", "")
                if d or e:
                    lines.append(f"- {d}：{e}" if d and e else f"- {d or e}")

    anchors = style.get("tone_anchors") or []
    if anchors:
        lines.append("语气锚点原句：")
        for a in anchors:
            if a:
                lines.append(f"- {a}")

    fmt_templates(style.get("humor"), "幽默套路")

    length = style.get("length")
    if isinstance(length, dict) and length.get("min") is not None:
        lines.append(f"篇幅：{length['min']}-{length['median']}-{length['max']} 字")

    avoid = style.get("avoid")
    if avoid and avoid != "（未提炼）":
        lines.append(f"要避免的：{avoid}")

    return "\n".join(lines)


# 两档风格模式的创作要求（沉淀的历次调优约束，两模板共用；动这里时两个模板都生效）
CREATION_REQUIREMENTS = """创作要求：
- 逐条细读示例，模仿它们说话的口吻、用词和句子的节奏；画像只是大方向，示例的味道优先
- 可以借用示例中的句式结构和说话方式，但不要整句照抄
- 本次文案的开头切入场景，以用户消息中指定的场景为准，必须围绕该场景展开，不得换成其他场景
- 文案以经营日常、真实感受和人情味为主，像参考文本那样娓娓道来，不要围绕产品卖点堆砌
- 感悟只写自己：写自己今天做了什么、身体和心里的状态，可以自嘲、可以说辛苦、可以讲原则；不要替客户或师傅下结论，不要出现"客户很满意""师傅夸""客户说好"这类他人评价
- 文案的主体是观念和感悟（对生意、信任、坚持、品质、人情、时节的朴实判断），当天发生的事只做一两句引子，不要写事件流水账；主题面要宽：参考样本里写信任、写坚持、写品质、写时节节气、写节日祝福、写感恩致谢、写晚归自嘲都有，像那样换着角度写，这次写了信任，下次就换坚持或时节，不要每次生成都围着同一个主题打转；参考文本是"格言体"，判断句是它的底色，但句式要多样：样本 85 条里"不是……，而是……"只出现 6 条，"……，才是……"也只偶尔出现，绝大多数文案用的是平实的断言句——"不是……，而是……"句式本篇最多出现一次，多数时候不要使用，更不能连排两句；观念可以直接断言、白描或对比表达
- 全程第一人称独白，像自言自语：不要借任何人之口说话（"师傅说""客户说"这类一律禁止），不要对"你"喊话，不要写"你…我…"对仗或任何广告式金句
- 如需提到产品，只用泛称（如"这批砖""咱家的砖""今天送的那车货"），绝不出现具体产品名、系列名、花色名、型号或参数
- 篇幅长短向示例看齐，别写太长或太短
- 只输出文案正文本身，不要任何解释说明
- 不要出现"风格画像""示例"等字眼"""


# ==========================================
# 风格模式：画像 v2 重构后缩成两档
# style: 新画像（三段句库/意象/锚点/humor）+ 6 条按子类型均衡抽的示例 + 温度 0.7（推荐，默认）
# none:  去画像，仅示例原文 + 温度 1.0
# ==========================================
STYLE_MODES = {
    "style": {"label": "风格画像", "entry_count": 6, "temperature": 0.7},
    "none": {"label": "仅示例", "entry_count": 6, "temperature": 1.0},
}

# style 模式：可执行画像 + 子类型均衡示例
PROMPT_STYLE_TMPL = """你是一位{identity}。你的任务是根据下面的【风格画像】和【风格示例】，模仿其风格创作新的文案。

【风格画像】
{style}

【风格示例】
{examples}

""" + CREATION_REQUIREMENTS

# none 模式：画像彻底退场，示例原文是唯一风格来源
PROMPT_NONE_TMPL = """你是一位{identity}。你的任务是根据【风格示例】，模仿其口吻创作新的文案。

【风格示例】
{examples}

""" + CREATION_REQUIREMENTS


# 画像缺失 identity 时的兜底身份（兼容旧画像文件）
DEFAULT_IDENTITY = "在瓷砖行业摸爬滚打多年的老搬砖人，日常就是发发朋友圈、接待顾客、跑工地送货，靠真诚和靠谱把生意做稳"


def _get_identity(style: dict | None) -> str:
    """从画像取作者身份，缺失时用默认值"""
    v = style.get("identity") if style else None
    if v and v != "（未提炼）":
        v = v.strip().rstrip("。.!！")
        # 清洗 LLM 可能加上的"你是一位"前缀（模板里已带）
        for p in ("你是一位", "你是一个", "你是"):
            if v.startswith(p):
                v = v[len(p):].lstrip()
                break
        return v
    return DEFAULT_IDENTITY


def _build_system_prompt(mode: str, style: dict | None, examples: str) -> str:
    """按风格模式组装 system prompt"""
    identity = _get_identity(style)
    if mode == "none":
        return PROMPT_NONE_TMPL.format(identity=identity, examples=examples)
    style_text = _format_style(style) if style else "（未提供画像）"
    return PROMPT_STYLE_TMPL.format(identity=identity, style=style_text, examples=examples)


# ==========================================
# 主题层：把"今天干了什么"整理成文案主题（可编辑的中间产物）
# 输出对齐场景池风格：一句话口语白描，绝不脑补细节
# ==========================================
TOPIC_PROMPT_TMPL = """你是给一位瓷砖店主帮忙的文案助手。店主随口说了句"今天干了什么"，请把它整理成一个简短的文案主题。

要求：
- 主题是一句口语化的白描，像"给客户送砖上门，卸货装车忙了一上午"这样，15-30 字
- 只整理店主原话，可以理顺语序、补足通顺，但不要添加任何想象出来的细节（动作、环境、心情、对话、人名、产品名都不许编）
- 只描述自己做了什么，不要出现"客户满意""师傅夸""客户说好"这类他人评价
- 不要写成广告语，不要分句、分行或加任何渲染
- 只输出主题本身，不要任何解释、前缀或后缀

示例：
输入：今天去客户家量尺寸
输出：上门给客户量方

店主的输入：
{input}
"""


def build_topic(input_text: str) -> str:
    """主题层：LLM 把"今天干了什么"提炼成文案主题"""
    text = (input_text or "").strip()
    if not text:
        raise ValueError("输入不能为空")
    llm = get_chat_model(temperature=0.7, streaming=False)
    resp = llm.invoke(TOPIC_PROMPT_TMPL.format(input=text))
    return resp.content.strip()


def generate_stream(text_type: str, style: dict | None = None, style_mode: str = "style", topic: str | None = None):
    """按类型生成文案，返回 token 迭代器（流式）
    topic: 用户输入"今天干了什么"生成的主题；为空时走随机场景池兜底
    """
    if text_type not in TYPES:
        raise ValueError(f"未知文案类型: {text_type}")
    mode = STYLE_MODES.get(style_mode)
    if not mode:
        raise ValueError(f"未知风格模式: {style_mode}")

    style = style or load_style()
    examples = _pick_examples(n=mode["entry_count"], style=style)
    if not examples:
        examples = "（无示例样本）"

    # 有主题用主题；无主题时：朋友圈走"纯感悟"模式（参考样本里大量无场景的纯感悟文案，
    # 不硬塞场景），小红书/销售话术仍走随机场景池（种草/话术需要具体场景支撑）
    if topic and topic.strip():
        scene_text, scene_instr = topic.strip(), "文案必须围绕这个主题展开，以它为核心，不得更换或另起场景。"
    elif text_type == "wechat_moment":
        scene_text, scene_instr = "（不限定场景）", "本次不限定具体场景：像参考样本中的纯感悟文案那样（如'信任这东西很珍贵，一旦给了，就不能辜负''该和深夜和解了，它见证了我的努力和成长''在平凡的日常里坚守热爱'），直接写一段道理或感悟，主体是观念和感悟；主题面要宽，信任、坚持、品质、时节、感恩、自嘲都可以，换着角度写，不要每次都是同一个主题；最多带一句经营日常做引子，不要编造具体事件流水账。"
    else:
        scene_text, scene_instr = _pick_scene(), "文案必须从这个场景切入展开，不得更换。"
    requirement = TYPES[text_type]["requirement"] + f"\n\n【本次主题】{scene_text}\n{scene_instr}"

    messages = [
        ("system", _build_system_prompt(style_mode, style, examples)),
        ("human", requirement),
    ]
    llm = get_chat_model(temperature=mode["temperature"], streaming=True)
    for chunk in llm.stream(messages):
        if chunk.content:
            yield chunk.content
