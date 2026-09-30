"""
メモエントリのデータモデル定義（Pydantic v2）
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


# ─── 既存カテゴリ（企業名分類用・後方互換のため保持） ────────────────────────

CATEGORIES = [
    "企業研究",
    "説明会",
    "OB訪問",
    "ES・履歴書",
    "面接",
    "選考結果",
    "その他",
]

# ─── メモ種別（AI による自動判定・構造化要約の分岐に使う） ─────────────────

MEMO_TYPES = [
    "企業研究",
    "ES・履歴書",
    "面接",
    "インターン",
    "選考結果",
]

MemoType = Literal["企業研究", "ES・履歴書", "面接", "インターン", "選考結果"]
SourceType = Literal["paste", "ocr", "manual"]


# ─── 種別ごとのセクション項目 ─────────────────────────────────────────────────
# AI が種別に応じて構造化する見出し。
# Streamlit の詳細表示・Google Sheets の列分割の両方で使う。

SECTIONS_BY_TYPE: dict[str, list[str]] = {
    "企業研究": [
        "企業概要", "事業内容", "強み・特徴", "求める人物像",
        "社風・社員", "志望動機材料", "懸念点", "次回確認事項",
        "質問内容", "回答内容", "志望度",
    ],
    "面接": [
        "面接日", "面接段階", "質問内容", "自分の回答",
        "面接官の反応", "良かった点", "改善点", "逆質問",
    ],
    "ES・履歴書": [
        "提出日", "設問", "設問種別",
        "使用エピソード", "アピールポイント", "提出文章",
        "企業が求める人物像", "評価されそうな点", "改善案", "不足している要素",
        "面接で深掘りされそうな点", "想定質問",
        "志望動機材料", "企業との接点",
    ],
    "インターン": [
        "開催日", "開催形式", "インターン概要", "プログラム内容",
        "企業理解", "社風・社員", "社員への質問", "社員の回答",
        "印象に残った話", "魅力に感じた点", "懸念点", "志望動機材料",
        "面接で使える内容", "選考優遇情報", "本選考への影響",
        "グループワーク振り返り", "自分の良かった点", "改善点",
    ],
    "選考結果": [
        "選考段階", "結果", "通知日",
        "通過要因", "落選要因",
        "良かった点", "改善点", "学び",
    ],
}

# Google Sheets 用の「全種別を合併したセクション列」（旧レイアウト読み取り・移行用に保持）
def _build_all_sections() -> list[str]:
    seen: list[str] = []
    for items in SECTIONS_BY_TYPE.values():
        for item in items:
            if item not in seen:
                seen.append(item)
    return seen

ALL_SECTION_COLUMNS: list[str] = _build_all_sections()
"""全種別を合併したセクション列名のリスト（旧統合レイアウトの互換用）"""


# ─── シート構成定義 ───────────────────────────────────────────────────────────
# 種別ごとに「使用するシート名」と「そのシートのセクション列」を定義する。
# これにより、種別ごとに専用シート・専用列構成を持てる（拡張可能）。
#
# 現状:
#   - 企業研究 → 既存の "memos" シートを企業研究専用として使う
#   - 面接 / ES / インターン → 将来、別シートとして追加可能（sheet_name を予約）
#
# 新しい種別用シートを増やすときは、このマップにエントリを足すだけでよい。

class SheetConfig:
    def __init__(self, sheet_name: str, sections: list[str]):
        self.sheet_name = sheet_name
        self.sections = sections


SHEET_CONFIG: dict[str, SheetConfig] = {
    "企業研究": SheetConfig(
        sheet_name="memos",   # 既存シートをそのまま企業研究用に使う（既存データ維持）
        sections=[
            "企業概要", "事業内容", "強み・特徴", "求める人物像",
            "社風・社員", "志望動機材料", "懸念点", "次回確認事項",
            "質問内容", "回答内容", "志望度",
        ],
    ),
    "面接": SheetConfig(
        sheet_name="面接",   # 企業研究とは別シートで管理
        sections=[
            "面接日", "面接段階", "質問内容", "自分の回答",
            "面接官の反応", "良かった点", "改善点", "逆質問",
        ],
    ),
    "インターン": SheetConfig(
        sheet_name="インターン",   # 企業研究・面接とは別シートで管理
        sections=[
            "開催日", "開催形式", "インターン概要", "プログラム内容",
            "企業理解", "社風・社員", "社員への質問", "社員の回答",
            "印象に残った話", "魅力に感じた点", "懸念点", "志望動機材料",
            "面接で使える内容", "選考優遇情報", "本選考への影響",
            "グループワーク振り返り", "自分の良かった点", "改善点",
        ],
    ),
    "ES・履歴書": SheetConfig(
        sheet_name="ES・履歴書",   # ES・履歴書専用シート
        sections=[
            "提出日", "設問", "設問種別",
            "使用エピソード", "アピールポイント", "提出文章",
            "企業が求める人物像", "評価されそうな点", "改善案", "不足している要素",
            "面接で深掘りされそうな点", "想定質問",
            "志望動機材料", "企業との接点",
        ],
    ),
    "選考結果": SheetConfig(
        sheet_name="選考結果",   # 選考結果専用シート
        sections=[
            "選考段階", "結果", "通知日",
            "通過要因", "落選要因",
            "良かった点", "改善点", "学び",
        ],
    ),
}

DEFAULT_MEMO_TYPE = "企業研究"
"""現在アプリが主対象とする種別（この種別のシート構成をデフォルトで使う）"""


# ─── メインモデル ─────────────────────────────────────────────────────────────

class MemoEntry(BaseModel):
    """スプレッドシートおよびAPIで扱うメモの完全モデル"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    memo_date: date | None = None
    company_name: str = "不明"
    category: str = "その他"
    tags: list[str] = Field(default_factory=list)
    source_type: SourceType = "paste"
    raw_text: str

    # ── メモ種別 ───────────────────────────────────────────────────────────
    memo_type: MemoType = "企業研究"
    """AIが判定したメモ種別（企業研究 / ES・履歴書 / 面接 / インターン / 選考結果）"""

    # ── Streamlit 詳細表示用 ───────────────────────────────────────────────
    detailed_summary: str = ""
    """見出し付きの詳細な構造化要約（Markdown 形式・Streamlit 表示用）"""

    # ── Google Sheets 列分割用 ─────────────────────────────────────────────
    sections: dict[str, str] = Field(default_factory=dict)
    """セクション名 → 要点（50字以内）の辞書。Sheets の項目別列に分割保存する。"""

    keywords: list[str] = Field(default_factory=list)
    """AIが抽出したキーワード（3〜10個）"""

    next_action: str = ""
    """AIが提案する次回アクション"""

    # ── 後方互換フィールド（旧データ読み込み用・非推奨） ────────────────────
    structured_summary: str = ""
    sheet_summary: str = ""
    summary: str = ""


# ─── リクエスト/レスポンスモデル ─────────────────────────────────────────────

class MemoCreateRequest(BaseModel):
    """POST /memos のリクエストボディ"""

    raw_text: str = Field(min_length=1, max_length=10000)
    company_name: str | None = None
    category: str | None = None
    memo_date: date | None = None


class MemoUpdateRequest(BaseModel):
    """PATCH /memos/{id} のリクエストボディ（すべて任意）"""

    company_name: str | None = None
    category: str | None = None
    memo_type: str | None = None
    detailed_summary: str | None = None
    sections: dict[str, str] | None = None
    keywords: list[str] | None = None
    next_action: str | None = None
    tags: list[str] | None = None


class MemoListResponse(BaseModel):
    total: int
    items: list[MemoEntry]


# ─── AI解析結果（内部利用） ───────────────────────────────────────────────────

class AnalysisResult(BaseModel):
    """Gemini から受け取る解析結果（構造化版）"""

    company_name: str = "不明"
    category: str = "その他"
    memo_type: MemoType = "企業研究"
    keywords: list[str] = Field(default_factory=list)
    next_action: str = ""
    detailed_summary: str = ""
    """見出し付き詳細要約（Streamlit 表示用・Markdown）"""
    sections: dict[str, str] = Field(default_factory=dict)
    """セクション名 → 要点（50字以内）。Google Sheets の列分割用"""
    is_partial: bool = False
    """AIレスポンスが途中で切れる等で不完全だった場合 True（要ユーザー確認）"""
