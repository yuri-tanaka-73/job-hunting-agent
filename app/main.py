"""
FastAPI エントリーポイント

起動方法:
    uvicorn app.main:app --reload --port 8000

Swagger UI:
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.api.memos import router as memos_router
from app.config import settings

# ─── ロギング設定 ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if not settings.is_production else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── FastAPI アプリ ───────────────────────────────────────────────────────────

app = FastAPI(
    title="就活メモ整理AIエージェント",
    description="テキスト入力 → AI要約 → Googleスプレッドシート保存 → 一覧表示",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── API ルーター ─────────────────────────────────────────────────────────────

app.include_router(memos_router, prefix="/api/v1")

# ─── 静的ファイル（フロントエンド） ──────────────────────────────────────────

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
_INDEX_HTML = os.path.join(_STATIC_DIR, "index.html")

if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_frontend() -> FileResponse:
        return FileResponse(_INDEX_HTML)

# ─── ヘルスチェック ───────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health_check() -> dict:
    return {"status": "ok", "env": settings.app_env}
