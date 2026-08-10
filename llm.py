# ==========================================
# llm.py — LLM 封装（LangChain + DashScope 兼容模式）
# ==========================================
from langchain_openai import ChatOpenAI

from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL


def get_chat_model(temperature: float = 0.7, streaming: bool = False):
    """创建 ChatOpenAI 实例（走 DashScope 兼容接口）"""
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        temperature=temperature,
        streaming=streaming,
    )
