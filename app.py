# ==========================================
# app.py — 瓷砖文案风格生成器（FastAPI 入口）
# 启动: rag_env/Scripts/python -m uvicorn app:app --port 8000
# ==========================================
import json
import logging

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import generator
import style_manager
from config import BASE_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="瓷砖文案风格生成器")


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# ---------- 页面 ----------

@app.get("/")
async def index():
    # no-cache：开发期前端频繁改动，避免浏览器缓存旧页面（看不到新功能）
    return FileResponse(BASE_DIR / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/sample")
async def sample(name: str):
    """读取单个样本内容（前端点击瓷片预览）"""
    data_dir = style_manager.DATA_DIR
    path = data_dir / style_manager._safe_name(name)
    if not path.is_file() or path.suffix.lower() not in style_manager.ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=404, content={"ok": False, "message": "文件不存在"})
    content = path.read_text(encoding="utf-8", errors="ignore")
    return {"ok": True, "name": path.name, "content": content[:20000]}


# ---------- 样本与画像 ----------

@app.get("/api/status")
async def status():
    style = style_manager.load_style()
    samples = style_manager.list_samples()
    return {
        "sample_count": len(samples),
        "samples": samples,
        "style_built": style is not None,
        "style": style,
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    saved = style_manager.save_sample_files(files)
    total = len(style_manager.list_samples())
    return {"saved": saved, "total": total,
            "message": f"已保存 {saved} 个样本" if saved else "没有新文件可保存（已存在或格式不支持，支持 .txt/.md）"}


@app.post("/api/build_style")
async def build_style(request: Request):
    try:
        body = await request.json()
        industry = body.get("industry")
    except Exception:
        industry = None
    try:
        style = style_manager.build_style(industry)
        return {"ok": True, "style": style}
    except Exception as e:
        logger.exception("构建画像失败")
        return JSONResponse(status_code=500, content={"ok": False, "message": str(e)})


@app.delete("/api/sample")
async def delete_sample(name: str):
    """删除单个样本（服务器部署版：直接删除，不保留归档）"""
    ok = style_manager.delete_sample(name)
    if not ok:
        return JSONResponse(status_code=404, content={"ok": False, "message": "文件不存在或格式不支持"})
    generator.reset_pools()  # 清示例池缓存，下次生成重新读取 data/
    total = len(style_manager.list_samples())
    msg = f"已删除参考文档：{name}"
    if total == 0:
        style_manager.clear_style()  # 没有生效样本了，画像一并清掉
        msg += "；已无生效样本，画像已重置"
    return {"ok": True, "message": msg, "total": total}


@app.delete("/api/reset")
async def reset():
    n = style_manager.clear_samples()
    style_manager.clear_style()
    return {"ok": True, "message": f"已清空 {n} 个样本和风格画像"}


# ---------- 主题层 ----------

@app.post("/api/build_topic")
async def build_topic(request: Request):
    """把'今天干了什么'提炼成文案主题"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"ok": False, "message": "请先输入今天干了什么"})
    try:
        topic = generator.build_topic(text)
        return {"ok": True, "topic": topic}
    except Exception as e:
        logger.exception("生成主题失败")
        return JSONResponse(status_code=500, content={"ok": False, "message": str(e)})


# ---------- 文案生成（SSE 流式） ----------

@app.post("/api/generate")
async def generate(request: Request):
    body = await request.json()
    text_type = body.get("type", "xiaohongshu")
    style_mode = body.get("style_mode", "style")
    topic = body.get("topic")

    if text_type not in generator.TYPES:
        return JSONResponse(status_code=400, content={"ok": False, "message": f"未知类型: {text_type}"})
    if style_mode not in generator.STYLE_MODES:
        return JSONResponse(status_code=400, content={"ok": False, "message": f"未知风格模式: {style_mode}"})

    style = style_manager.load_style()
    if not style:
        # 无画像时先尝试用现有样本即时构建；仍失败则用通用风格生成
        try:
            style = style_manager.build_style()
        except Exception:
            style = None

    def event_stream():
        yield _sse({"type": "start"})
        try:
            for chunk in generator.generate_stream(text_type, style, style_mode, topic):
                yield _sse({"type": "delta", "content": chunk})
            yield _sse({"type": "done"})
        except Exception as e:
            logger.exception("生成失败")
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
