# ==========================================
# style_manager.py — 样本管理与风格画像构建
# 1. 保存前端上传的样本文本到 data/
# 2. 用 LLM 分析样本 → 生成可执行风格画像 JSON → 存 style/tile_style.json
#
# 画像 v2（可执行数据，取代 v1 的 7 维抽象描述）：
#   抽象描述是"对风格的评论"，模型照着写反而没味道（A/B 实验结论）；
#   可执行数据是"模板+原句"，模型拿到就能照着套。
# ==========================================
import json
import logging
import re
from datetime import datetime

from config import ALLOWED_EXTENSIONS, DATA_DIR, STYLE_DIR, STYLE_FILE
from llm import get_chat_model

logger = logging.getLogger(__name__)

# 子类型：示例抽取时按类均衡轮换（不连续抽同类、一轮内不重复）
SUBTYPE_NAMES = ["状态型", "观念型", "感恩型", "自嘲型"]

# 分析输入时单条样本最大字符数（防超长，样本最长 ~100 字，600 足够）
MAX_ENTRY_CHARS = 600

# 画像分析提示词：一次 LLM 调用产出全部画像字段 + 每条样本的子类型分类
STYLE_ANALYSIS_PROMPT = """你是资深文案风格分析师。下面是同一位作者的朋友圈文案样本（共 {count} 条，每条前有编号）。请完成两件事，只输出一个 JSON 对象。

【任务一】提炼可执行的写作画像，包含字段：
- structure: 字符串，整篇的结构骨架，如"开头1句状态引子 → 中间2-4句观念断言 → 结尾感谢/自嘲/承诺+表情"
- structure_example: 样本中能体现该结构的一条原句（照抄原文）
- openings: 数组，开头句库，每条 {{"pattern", "example"}}。pattern 是开头句式的骨架（如"该……了""早安[咖啡]"），example 是样本中该句式的原句
- core_templates: 数组，观念主体句库，同上，用于中间观念断言部分（如"不是……，而是……"）
- closings: 数组，收尾句库，同上，用于结尾（如"感谢……。[握手][握手]"）
- imagery: 数组，意象库，每条 {{"domain", "examples"}}。domain 是意象域（如"工地/砖"），examples 是该域下的具体词或短语
- tone_anchors: 数组，3 句最能代表作者语气与价值观的原句，主题必须分散（如信任、坚持、时节/晚归各选一），不要都选自同一个主题
- humor: 数组，幽默套路，每条 {{"pattern", "example"}}。pattern 用一句话描述幽默/自嘲套路（如"拿自己的辛苦与别人的享受对比，加数学梗自嘲"），example 是样本中的原句
- avoid: 字符串，作者刻意避免的表达

【任务二】给每条样本分类，四个子类型：
- 状态型：白描"今天干了什么"（送砖/量方/收工/晚归等）
- 观念型：判断句格言（对生意、信任、坚持的朴实断言）
- 感恩型：以感恩/致谢为主体或收尾
- 自嘲型：自嘲幽默（苦中作乐）
输出字段 subtypes：{{"1": "观念型", "2": "状态型", ...}}，键是样本编号，值只能是这四个类型之一。

硬性要求：
- openings / core_templates / closings / humor / tone_anchors 里的 example 和 structure_example 必须来自样本原文，照抄，禁止改写、禁止新编
- 只输出一个 JSON 对象（含任务一全部字段 + subtypes），不要输出任何其他文字

【样本】
{numbered_samples}
"""


# ---------- 工具 ----------

def _safe_name(original: str) -> str:
    """清洗文件名，防路径穿越"""
    name = original.replace("\\", "/").split("/")[-1]
    return re.sub(r"[^\w一-鿿.-]", "_", name)


def _split_entries(text: str) -> list[str]:
    """按 2 个以上空行切分独立条目（1 个空行视为篇内换行），过滤碎片"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)  # 去行尾空白
    entries = [e.strip() for e in re.split(r"\n{3,}", text) if e.strip()]
    return [e for e in entries if len(re.findall(r"[一-鿿]", e)) >= 4]


# ---------- 样本管理 ----------

def list_samples() -> list[dict]:
    """列出已上传的样本文件信息"""
    if not DATA_DIR.exists():
        return []
    items = []
    for p in sorted(DATA_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            items.append({
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return items


def save_sample_files(uploaded_files) -> int:
    """保存上传的样本文件，返回成功保存的数量（跳过重复名）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for f in uploaded_files:
        suffix = f.filename.rsplit(".", 1)[-1].lower()
        if f.filename and ("." + suffix) in ALLOWED_EXTENSIONS:
            path = DATA_DIR / _safe_name(f.filename)
            if not path.exists():  # 已存在则跳过，避免覆盖
                path.write_bytes(f.file.read())
                saved += 1
    return saved


def clear_samples() -> int:
    """清空所有样本文件，返回删除数量"""
    count = 0
    if DATA_DIR.exists():
        for p in DATA_DIR.glob("*"):
            if p.is_file():
                p.unlink()
                count += 1
    return count


def _read_entries() -> list[str]:
    """读取全部样本 → 按空行切分条目，返回条目列表（供画像构建与示例池共用）"""
    entries = []
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.glob("*")):
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
                entries.extend(_split_entries(p.read_text(encoding="utf-8", errors="ignore")))
    return entries


# ---------- 风格画像 ----------

def load_style() -> dict | None:
    """读取已保存的风格画像"""
    if STYLE_FILE.exists():
        try:
            return json.loads(STYLE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("风格画像文件损坏，将重新构建")
    return None


def _parse_style_json(raw: str) -> dict:
    """从 LLM 输出中提取 JSON（容错：剔除代码块包裹）"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 取第一个 { 到最后一个 } 再试一次
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _norm_str(v) -> str:
    """字段缺失/空值 → 占位符"""
    if isinstance(v, str) and v.strip():
        return v.strip()
    return "（未提炼）"


def _validate_subtypes(raw, entry_count: int) -> dict | None:
    """校验 LLM 的样本分类：键必须覆盖 1..N，值必须是四类之一；失败返回 None"""
    if not isinstance(raw, dict):
        return None
    mapping = {}
    for k, v in raw.items():
        if not str(k).isdigit() or not 1 <= int(k) <= entry_count or v not in SUBTYPE_NAMES:
            return None
        mapping[int(k)] = v
    if set(mapping) != set(range(1, entry_count + 1)):
        return None
    return mapping


def _length_stats(entries: list[str]) -> dict:
    """程序统计每条字符数 → min/median/max（数数即可，稳定，不让 LLM 做）"""
    chars = sorted(len(re.findall(r"[一-鿿]", e)) for e in entries)
    n = len(chars)
    if n % 2:
        median = chars[n // 2]
    else:
        median = (chars[n // 2 - 1] + chars[n // 2]) // 2
    return {"min": chars[0], "median": median, "max": chars[-1]}


def _make_identity(industry: str | None) -> str:
    """身份字段：由用户输入的行业拼接（不再让 LLM 提炼，效果不稳）"""
    ind = (industry or "").strip() or "瓷砖"
    ind = ind.rstrip("行业").strip() or "瓷砖"
    return f"在{ind}行业摸爬滚打多年的老手，日常就是发发朋友圈、接待顾客、跑工地送货，靠真诚和靠谱把生意做稳"


def build_style(industry: str | None = None) -> dict:
    """用 LLM 分析全部样本 → 生成可执行画像 → 保存并返回
    流程：程序切条目/算篇幅 → LLM 一次调用出画像字段+子类型分类 → 拼装保存
    industry: 用户输入的行业，拼成画像的 identity（身份）字段"""
    entries = _read_entries()
    if not entries:
        raise ValueError("没有可分析的样本，请先上传样本文本")

    numbered = "\n".join(f"第{i}条：{e[:MAX_ENTRY_CHARS]}" for i, e in enumerate(entries, 1))
    prompt = STYLE_ANALYSIS_PROMPT.format(count=len(entries), numbered_samples=numbered)

    llm = get_chat_model()
    parsed = _parse_style_json(llm.invoke(prompt).content)

    # 子类型分类必须完整（键覆盖全部编号、值合法），失败重试一次
    mapping = _validate_subtypes(parsed.get("subtypes"), len(entries))
    if mapping is None:
        logger.warning("样本分类不完整，重试一次")
        parsed = _parse_style_json(llm.invoke(prompt).content)
        mapping = _validate_subtypes(parsed.get("subtypes"), len(entries))
    if mapping is None:
        raise ValueError("画像构建失败：样本子类型分类不完整，请重试")

    # 子类型 → 原句列表（生成时按类均衡抽示例用）
    subtype = {name: [] for name in SUBTYPE_NAMES}
    for idx, cls in sorted(mapping.items()):
        subtype[cls].append(entries[idx - 1])

    style = {
        "identity": _make_identity(industry),
        "structure": _norm_str(parsed.get("structure")),
        "structure_example": _norm_str(parsed.get("structure_example")),
        "openings": parsed.get("openings") or [],
        "core_templates": parsed.get("core_templates") or [],
        "closings": parsed.get("closings") or [],
        "imagery": parsed.get("imagery") or [],
        "tone_anchors": parsed.get("tone_anchors") or [],
        "humor": parsed.get("humor") or [],
        "avoid": _norm_str(parsed.get("avoid")),
        "length": _length_stats(entries),
        "subtype": subtype,
        "sample_count": len(entries),
        "source_files": [s["name"] for s in list_samples()],
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    STYLE_DIR.mkdir(parents=True, exist_ok=True)
    STYLE_FILE.write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
    return style


def clear_style() -> None:
    """删除风格画像"""
    if STYLE_FILE.exists():
        STYLE_FILE.unlink()
