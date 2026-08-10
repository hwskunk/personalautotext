# 瓷砖文案生成器（personalaitext）

上传文案样本 → AI 提炼风格画像 → 一键生成瓷砖销售文案（小红书种草 / 朋友圈短文案 / 销售话术）。

## 启动

```bash
# 首次需确认 rag_env 已安装依赖（requirements.txt）
# 启动服务
C:\Users\15613\Desktop\LangchainRAGtrain\rag_env\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000

## 使用流程

1. **上传样本**：把文案文件（.txt / .md）拖进上传区，或点击选择（可多选）
2. **生成画像**：点击「生成画像」，AI 基于全部样本提炼 7 个维度的风格画像
3. **生成文案**：点击类型按钮，文案流式输出

## 目录结构

```
personalaitext/
├── app.py            # FastAPI 入口 + API
├── config.py         # 配置（模型、目录、.env）
├── llm.py            # LangChain + DashScope 封装
├── style_manager.py  # 样本管理 + 风格画像构建
├── generator.py      # 三类文案生成（流式）
├── static/index.html # 前端页面
├── data/             # 上传的样本文件
├── style/            # 风格画像 tile_style.json
├── .env              # DashScope API Key（复制自 MyIntroduce）
└── requirements.txt
```

## 说明

- 当前 `data/` 里有 2 篇测试样本、`style/` 有基于它们生成的画像，可直接点生成体验效果
- 换成自己的真实样本：页面底部「清空样本与画像」→ 上传真实样本 → 重新生成画像
- 模型默认 `qwen-plus`，可在 `.env` 的 `LLM_MODEL_NAME` 调整
