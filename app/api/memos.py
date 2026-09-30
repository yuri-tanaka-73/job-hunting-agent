"""
メモ CRUD エンドポイント

GET  /api/v1/memos          - 一覧取得（フィルタ対応）
POST /api/v1/memos          - 新規登録（AI分析 → Sheets保存）
GET  /api/v1/memos/{id}     - 詳細取得
PATCH /api/v1/memos/{id}    - 更新
DELETE /api/v1/memos/{id}   - 削除
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from models.memo import (
    MemoCreateRequest,
    MemoEntry,
    MemoListResponse,
    MemoUpdateRequest,
)
from services.ai_service import analyze_memo
from services.sheets_service import SheetsService, get_sheets_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memos", tags=["memos"])


# ─── 一覧取得 ─────────────────────────────────────────────────────────────────

@router.get("", response_model=MemoListResponse)
async def list_memos(
    company: str | None = Query(default=None, description="企業名でフィルタ（部分一致）"),
    category: str | None = Query(default=None, description="カテゴリでフィルタ"),
    limit: int = Query(default=50, ge=1, le=200, description="取得件数"),
    offset: int = Query(default=0, ge=0, description="スキップ件数"),
    sheets: SheetsService = Depends(get_sheets_service),
) -> MemoListResponse:
    """登録済みメモの一覧を返す。新しい順にソートされる。"""
    memos = sheets.get_all_memos()

    # フィルタリング
    if company:
        memos = [m for m in memos if company.lower() in m.company_name.lower()]
    if category:
        memos = [m for m in memos if m.category == category]

    # 新しい順にソート
    memos.sort(key=lambda m: m.created_at, reverse=True)

    total = len(memos)
    items = memos[offset : offset + limit]

    return MemoListResponse(total=total, items=items)


# ─── 新規登録 ─────────────────────────────────────────────────────────────────

@router.post("", response_model=MemoEntry, status_code=status.HTTP_201_CREATED)
async def create_memo(
    req: MemoCreateRequest,
    sheets: SheetsService = Depends(get_sheets_service),
) -> MemoEntry:
    """
    テキストを受け取り、AIで分析してスプレッドシートに保存する。

    1. Gemini API で企業名推定・カテゴリ分類・要約を生成
    2. MemoEntry を生成
    3. Google Sheets に1行追記
    """
    logger.info("Creating memo (length=%d)", len(req.raw_text))

    # AI分析
    analysis = await analyze_memo(
        raw_text=req.raw_text,
        company_hint=req.company_name,
        category_hint=req.category,
    )

    # メモエントリ生成（ユーザーのヒントがあれば優先）
    memo = MemoEntry(
        raw_text=req.raw_text,
        company_name=req.company_name or analysis.company_name,
        category=req.category or analysis.category,
        tags=analysis.tags,
        summary=analysis.summary,
        source_type="paste",
        memo_date=req.memo_date,
    )

    # Sheets に保存
    sheets.append_memo(memo)
    logger.info("Memo saved: id=%s company=%s", memo.id, memo.company_name)

    return memo


# ─── 詳細取得 ─────────────────────────────────────────────────────────────────

@router.get("/{memo_id}", response_model=MemoEntry)
async def get_memo(
    memo_id: str,
    sheets: SheetsService = Depends(get_sheets_service),
) -> MemoEntry:
    memos = sheets.get_all_memos()
    for memo in memos:
        if memo.id == memo_id:
            return memo
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memo not found")


# ─── 更新 ─────────────────────────────────────────────────────────────────────

@router.patch("/{memo_id}", response_model=MemoEntry)
async def update_memo(
    memo_id: str,
    req: MemoUpdateRequest,
    sheets: SheetsService = Depends(get_sheets_service),
) -> MemoEntry:
    memos = sheets.get_all_memos()
    target: MemoEntry | None = None
    for memo in memos:
        if memo.id == memo_id:
            target = memo
            break

    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memo not found")

    # 変更フィールドのみ上書き
    update_data = req.model_dump(exclude_none=True)
    updated = target.model_copy(
        update={**update_data, "updated_at": datetime.now(timezone.utc)}
    )

    sheets.update_memo(updated)
    return updated


# ─── 削除 ─────────────────────────────────────────────────────────────────────

@router.delete("/{memo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memo(
    memo_id: str,
    sheets: SheetsService = Depends(get_sheets_service),
) -> None:
    try:
        sheets.delete_memo(memo_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memo not found")
