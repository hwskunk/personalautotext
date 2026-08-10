# ==========================================
# generator.py — 文案生成（三种类型 × 三种风格模式，流式输出）
# 生成时 = 风格模式(画像/示例组合) + 类型要求
# ==========================================
import random
import re

from config import ALLOWED_EXTENSIONS, DATA_DIR
from llm import get_chat_model
from style_manager import STYLE_FIELDS, load_style, read_all_samples

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
            "1. 主体是观念断言：对生意、信任、坚持、辛苦的朴实判断，像'信任这东西很珍贵，一旦给了，就不能辜负''好的生意，从来不是靠低价内卷''晚归，不是因为夜色迷人'\n"
            "2. 当天的状态只做引子，最多一两句带过，不要写事件流水账（不要'茶水烧了几遍''摸了几块砖''数到第几张'这类过程细节）\n"
            "3. 多用判断句：'不是……，而是……''……，才是……''……，是……，也是……''别人……，而我……''把……做……'，模仿参考文本的句式节奏\n"
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
ENTRY_COUNT = 3

_example_pool: list[str] = []


def _split_entries(text: str) -> list[str]:
    """按 2 个以上空行切分独立条目（1 个空行视为篇内换行），过滤碎片"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)  # 去行尾空白
    entries = [e.strip() for e in re.split(r"\n{3,}", text) if e.strip()]
    return [e for e in entries if len(re.findall(r"[一-鿿]", e)) >= 4]


def _refill_pool() -> None:
    """重建示例条目池：读取全部样本 → 切分条目 → 洗牌"""
    global _example_pool
    _example_pool = []
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
                _example_pool.extend(_split_entries(p.read_text(encoding="utf-8", errors="ignore")))
    random.shuffle(_example_pool)


def _pick_examples(n: int = ENTRY_COUNT) -> str:
    """从示例条目池中洗牌轮换取 n 条（池空自动重建，一轮内不重复）"""
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
    return "\n\n".join(f"【示例】\n{e[:MAX_ENTRY_CHARS]}" for e in picked)


def _format_style(style: dict) -> str:
    """把风格画像格式化为提示词文本"""
    lines = []
    labels = {
        "tone": "语气",
        "vocabulary": "词汇偏好",
        "sentence_style": "句式习惯",
        "structure": "结构套路",
        "title_style": "标题风格",
        "length_guide": "篇幅与节奏",
        "avoid": "要避免的",
    }
    for field in STYLE_FIELDS:
        v = style.get(field)
        if v and v != "（未提炼）":
            lines.append(f"- {labels.get(field, field)}：{v}")
    return "\n".join(lines)


SYSTEM_PROMPT_TMPL = """你是一位{identity}。你的任务是根据【风格画像】和【风格示例】，模仿其风格创作新的文案。

【风格画像】
{style}

【风格示例】
{examples}

创作要求：
- 严格遵循风格画像中的语气、词汇、句式、结构，不要偏离
- 风格示例只用于学习语气、用词、句式节奏，绝不照抄示例中的句子、产品名、具体场景或比喻
- 本次文案的开头切入场景，以用户消息中指定的场景为准，必须围绕该场景展开，不得换成其他场景
- 文案以经营日常、真实感受和人情味为主，像参考文本那样娓娓道来，不要围绕产品卖点堆砌
- 感悟只写自己：写自己今天做了什么、身体和心里的状态，可以自嘲、可以说辛苦、可以讲原则；不要替客户或师傅下结论，不要出现"客户很满意""师傅夸""客户说好"这类他人评价
- 文案的主体是观念和感悟（对生意、信任、坚持的朴实断言），当天发生的事只做一两句引子，不要写事件流水账；参考文本是"格言体"，多用"不是……，而是……""……，才是……"这类判断句
- 全程第一人称独白，像自言自语：不要借任何人之口说话（"师傅说""客户说"这类一律禁止），不要对"你"喊话，不要写"你…我…"对仗或任何广告式金句
- 如需提到产品，只用泛称（如"这批砖""咱家的砖""今天送的那车货"），绝不出现具体产品名、系列名、花色名、型号或参数
- 只输出文案正文本身，不要任何解释说明
- 不要出现"风格画像""示例"等字眼
"""


# ==========================================
# 风格模式：A/B 实验产物（人味排查结论：画像越抽象，人味越少）
# full: 完整 7 维画像 + 3 条示例 + 温度 1.0（原方案）
# none: 去画像，仅示例原文 + 温度 1.0
# slim: 精简画像(tone/avoid) + 6 条示例 + 温度 0.7（推荐，默认）
# ==========================================
STYLE_MODES = {
    "full": {"label": "完整画像", "entry_count": 3, "temperature": 1.0},
    "none": {"label": "仅示例", "entry_count": 6, "temperature": 1.0},
    "slim": {"label": "精简画像", "entry_count": 6, "temperature": 0.7},
}

# none 模式：画像彻底退场，示例原文是唯一风格来源
PROMPT_NONE_TMPL = """你是一位{identity}。你的任务是根据【风格示例】，模仿其口吻创作新的文案。

【风格示例】
{examples}

创作要求：
- 风格示例是唯一的风格来源：逐条仔细读，模仿它们说话的口吻、用词和句子的节奏
- 可以借用示例中的句式结构和说话方式，但不要整句照抄
- 本次文案的开头切入场景，以用户消息中指定的场景为准，必须围绕该场景展开，不得换成其他场景
- 文案以经营日常、真实感受和人情味为主，像参考文本那样娓娓道来，不要围绕产品卖点堆砌
- 感悟只写自己：写自己今天做了什么、身体和心里的状态，可以自嘲、可以说辛苦、可以讲原则；不要替客户或师傅下结论，不要出现"客户很满意""师傅夸""客户说好"这类他人评价
- 文案的主体是观念和感悟（对生意、信任、坚持的朴实断言），当天发生的事只做一两句引子，不要写事件流水账；参考文本是"格言体"，多用"不是……，而是……""……，才是……"这类判断句
- 全程第一人称独白，像自言自语：不要借任何人之口说话（"师傅说""客户说"这类一律禁止），不要对"你"喊话，不要写"你…我…"对仗或任何广告式金句
- 如需提到产品，只用泛称（如"这批砖""咱家的砖""今天送的那车货"），绝不出现具体产品名、系列名、花色名、型号或参数
- 只输出文案正文本身，不要任何解释说明
"""

# slim 模式：画像只留 tone/avoid 两行当大方向，细节以示例为准
PROMPT_SLIM_TMPL = """你是一位{identity}。你的任务是根据下面的【风格画像】和【风格示例】，模仿其风格创作新的文案。

【风格画像】（只是大方向提示，细节以示例为准）
{tone}
{avoid}

【风格示例】
{examples}

创作要求：
- 逐条细读示例，模仿它们说话的口吻、用词和句子的节奏；画像只是大方向，示例的味道优先
- 可以借用示例中的句式结构和说话方式，但不要整句照抄
- 本次文案的开头切入场景，以用户消息中指定的场景为准，必须围绕该场景展开，不得换成其他场景
- 文案以经营日常、真实感受和人情味为主，像参考文本那样娓娓道来，不要围绕产品卖点堆砌
- 感悟只写自己：写自己今天做了什么、身体和心里的状态，可以自嘲、可以说辛苦、可以讲原则；不要替客户或师傅下结论，不要出现"客户很满意""师傅夸""客户说好"这类他人评价
- 文案的主体是观念和感悟（对生意、信任、坚持的朴实断言），当天发生的事只做一两句引子，不要写事件流水账；参考文本是"格言体"，多用"不是……，而是……""……，才是……"这类判断句
- 全程第一人称独白，像自言自语：不要借任何人之口说话（"师傅说""客户说"这类一律禁止），不要对"你"喊话，不要写"你…我…"对仗或任何广告式金句
- 如需提到产品，只用泛称（如"这批砖""咱家的砖""今天送的那车货"），绝不出现具体产品名、系列名、花色名、型号或参数
- 只输出文案正文本身，不要任何解释说明
"""


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
    if mode == "full":
        style_text = _format_style(style) if style else "（未提供画像，请参考示例的通用营销风格）"
        return SYSTEM_PROMPT_TMPL.format(identity=identity, style=style_text, examples=examples)
    if mode == "none":
        return PROMPT_NONE_TMPL.format(identity=identity, examples=examples)
    tone = style.get("tone") if style else None
    avoid = style.get("avoid") if style else None
    tone_line = f"- 语气：{tone}" if tone and tone != "（未提炼）" else "- 语气：真诚质朴、不端着"
    avoid_line = f"- 要避免的：{avoid}" if avoid and avoid != "（未提炼）" else ""
    return PROMPT_SLIM_TMPL.format(identity=identity, tone=tone_line, avoid=avoid_line, examples=examples)


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


def generate_stream(text_type: str, style: dict | None = None, style_mode: str = "slim", topic: str | None = None):
    """按类型生成文案，返回 token 迭代器（流式）
    topic: 用户输入"今天干了什么"生成的主题；为空时走随机场景池兜底
    """
    if text_type not in TYPES:
        raise ValueError(f"未知文案类型: {text_type}")
    mode = STYLE_MODES.get(style_mode)
    if not mode:
        raise ValueError(f"未知风格模式: {style_mode}")

    style = style or load_style()
    examples = _pick_examples(n=mode["entry_count"])
    if not examples:
        examples = "（无示例样本）"

    # 有主题用主题，没有则注入随机开场场景（避免模型凭先验选同一个开场）
    if topic and topic.strip():
        scene_text, scene_instr = topic.strip(), "文案必须围绕这个主题展开，以它为核心，不得更换或另起场景。"
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
