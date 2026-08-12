# ==========================================
# config.py — 全局配置
# ==========================================
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# DashScope 兼容模式（阿里云灵积）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = os.getenv("LLM_MODEL_NAME", "qwen-plus")      # 主题生成 + 文案生成
FAST_LLM_MODEL = os.getenv("FAST_LLM_MODEL_NAME", "qwen-turbo")

# DeepSeek 官方 API（画像构建专用：deepseek-chat 快且画像质量好，实测 9.1s vs v4-pro 175.3s）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
STYLE_MODEL = os.getenv("STYLE_MODEL_NAME", "deepseek-chat")

# 数据目录：上传的样本文本 / 生成的风格画像
DATA_DIR = BASE_DIR / "data"
STYLE_DIR = BASE_DIR / "style"
STYLE_FILE = STYLE_DIR / "tile_style.json"

# 允许上传的文件类型
ALLOWED_EXTENSIONS = {".txt", ".md"}
