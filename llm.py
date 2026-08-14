# ==========================================
# llm.py — LLM 封装（LangChain + DashScope 兼容模式 / DeepSeek 官方 API）
# ==========================================
from langchain_openai import ChatOpenAI

from config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    LLM_MODEL,
    STYLE_MODEL,
)


def get_chat_model(temperature: float = 0.7, streaming: bool = False):
    """创建 ChatOpenAI 实例（走 DashScope 兼容接口），主题生成 + 文案生成用"""
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        temperature=temperature,
        streaming=streaming,
    )


def get_style_model(temperature: float = 0.7, streaming: bool = False):
    """画像构建专用：走 DeepSeek 官方 API（deepseek-chat），实测快 19 倍且画像质量更好"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写")
    return ChatOpenAI(
        model=STYLE_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
        streaming=streaming,
        # 画像 JSON 较长（样本多时输出可超 DeepSeek 默认 4096，会被截断损坏），放到上限 8192
        max_tokens=8192,
    )


def get_topic_model(temperature: float = 0.7, streaming: bool = False):
    """主题生成专用：走 DeepSeek 官方 API（deepseek-chat）。
    一句话整理是轻任务，用快模型（实测 0.7s vs deepseek-v4-pro 15.5s，质量达标）"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写")
    return ChatOpenAI(
        model=STYLE_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
        streaming=streaming,
        max_tokens=8192,
    )
