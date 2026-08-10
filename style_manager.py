# ==========================================
# style_manager.py — 样本管理与风格画像构建
# 1. 保存前端上传的样本文本到 data/
# 2. 用 LLM 分析样本 → 生成风格画像 JSON → 存 style/tile_style.json
# ==========================================
import json
import logging
import re
from datetime import datetime

from config import ALLOWED_EXTENSIONS, DATA_DIR, STYLE_DIR, STYLE_FILE
from llm import get_chat_model

logger = logging.getLogger(__name__)

# 画像字段：前端摘要展示与生成 prompt 都依赖它
STYLE_FIELDS = [
    "identity",       # 作者身份（行业/角色/日常，动态生成 system prompt 角色句）
    "tone",           # 整体语气
    "vocabulary",     # 词汇偏好
    "sentence_style", # 句式习惯
    "structure",      # 结构套路
    "title_style",    # 标题风格
    "length_guide",   # 篇幅与节奏
    "avoid",          # 要避免的
]

# 画像分析提示词
STYLE_ANALYSIS_PROMPT = """你是一位资深文案风格分析师。请仔细阅读用户提供的所有【样本文本】（它们是同一类产品的营销文案），提炼出这套文案的写作风格画像。

分析维度（每个维度给出具体、可操作的描述，不要泛泛而谈）：
1. tone：整体语气与情感基调（如亲切/活泼/专业/夸张/真诚…），并用样本中的实例说明
2. vocabulary：词汇偏好（emoji 使用密度与类型、网络热词、感叹词、口语化程度、专业术语的使用）
3. sentence_style：句式习惯（长短句搭配、排比、设问、省略句、第二人称使用）
4. structure：结构套路（开头如何抓人、痛点场景如何引出、产品亮点如何展开、结尾如何促单/引导，是否使用话题标签）
5. title_style：标题的写法（数字式/悬念式/情绪式/疑问式…）
6. length_guide：每篇的篇幅范围、段落数量、每段大致长度
7. avoid：这套风格中刻意避免的（如过度夸张、生硬推销、复杂术语…）

要求：
- 全部用中文输出
- 只输出一个 JSON 对象，字段名为 tone / vocabulary / sentence_style / structure / title_style / length_guide / avoid
- 每个字段值是一段 1-3 句的中文描述，直接可读，不要嵌套对象
- 不要输出任何 JSON 之外的文字

【样本文本】
{samples}
"""

# 样本分析时单文件最大字符数（防超长）
MAX_SAMPLE_CHARS = 3000


def _safe_name(original: str) -> str:
    """清洗文件名，防路径穿越"""
    name = original.replace("\\", "/").split("/")[-1]
    return re.sub(r"[^\w一-鿿.-]", "_", name)


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


def read_all_samples() -> str:
    """拼接全部样本内容用于分析，每篇截断防止超长"""
    if not DATA_DIR.exists():
        return ""
    parts = []
    for p in sorted(DATA_DIR.glob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                parts.append(f"--- 样本《{p.name}》---\n{text[:MAX_SAMPLE_CHARS]}")
    return "\n\n".join(parts)


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


def build_style(industry: str | None = None) -> dict:
    """用 LLM 分析全部样本 → 生成风格画像 → 保存并返回
    industry: 用户输入的行业，拼成画像的 identity（身份）字段"""
    samples = read_all_samples()
    if not samples:
        raise ValueError("没有可分析的样本，请先上传样本文本")

    llm = get_chat_model()
    resp = llm.invoke(STYLE_ANALYSIS_PROMPT.format(samples=samples))
    style = _parse_style_json(resp.content)

    # 规范化：只保留认识的字段，补默认值
    normalized = {}
    for field in STYLE_FIELDS:
        v = style.get(field)
        normalized[field] = str(v).strip() if v else "（未提炼）"

    # 身份字段：由用户输入的行业拼接（不再让 LLM 提炼，效果不稳）
    ind = (industry or "").strip() or "瓷砖"
    ind = ind.rstrip("行业").strip() or "瓷砖"
    normalized["identity"] = f"在{ind}行业摸爬滚打多年的老手，日常就是发发朋友圈、接待顾客、跑工地送货，靠真诚和靠谱把生意做稳"
    normalized["sample_count"] = len(list_samples())
    normalized["source_files"] = [s["name"] for s in list_samples()]
    normalized["built_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    STYLE_DIR.mkdir(parents=True, exist_ok=True)
    STYLE_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def clear_style() -> None:
    """删除风格画像"""
    if STYLE_FILE.exists():
        STYLE_FILE.unlink()
