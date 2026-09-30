"""
就活AIエージェント（RAG 構成のオーケストレーション層）

ユーザーからは「1人の就活AIエージェント」に見える単一の窓口 ask_agent() を提供する。
内部では次の流れで動く（RAG）:

  1. Retriever で質問に関連する保存済みメモだけを検索（全件は Gemini に送らない）
  2. 選ばれた少数のメモを「コンテキスト」として整形
  3. 1つのシステムプロンプトに 7 つの支援機能を内包させて Gemini に渡す
     （次の行動提案 / 面接対策 / 志望動機作成 / 自己分析 / 企業比較 /
       メモ整理 / 就活傾向分析）
  4. 回答テキストと「参照したメモ（企業名・種別・日付）」を返す

将来データ件数が増えても、Retriever を差し替えるだけで対応できる
（retriever_service.get_retriever を参照）。
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field

import google.generativeai as genai

from config import settings
from models.memo import MemoEntry
from services.ai_service import AIAnalysisError, _classify_api_error
from services.retriever_service import Retriever, RetrievedMemo, get_retriever

logger = logging.getLogger(__name__)


# ─── エージェントの応答 ───────────────────────────────────────────────────────

@dataclass
class AgentResponse:
    """エージェントの回答と、根拠として参照したメモ。"""

    answer: str
    references: list[RetrievedMemo] = field(default_factory=list)
    # 透明性のための「参考情報」表示用リスト（長期記憶・メモ・お気に入り等の由来）
    info_sources: list[str] = field(default_factory=list)


# ─── システムプロンプト（7機能を1人のエージェントに内包） ─────────────────────

_AGENT_SYSTEM_PROMPT = """\
あなたはユーザー専属の「就活AIエージェント」です。
ユーザーが保存してきた就活メモ（企業研究・面接記録・ES/履歴書・インターン記録・
選考結果・OCRで取り込んだ紙メモ など）と、これまでの会話から蓄積した
「長期記憶（ユーザーの就活プロフィール）」を横断的に活用し、就活全体を支援します。

あなたは以下すべての役割を1人で担います。質問内容から最も適切な支援を選んでください:
1. 次の行動提案（今やるべきことを具体的に）
2. 面接対策（想定質問・回答方針・逆質問）
3. 志望動機の作成支援（メモの事実に基づく）
4. 自己分析支援（強み・弱み・経験の言語化）
5. 企業比較（複数企業の特徴・志望度の整理）
6. メモ整理（散らばった情報の要約・構造化）
7. 就活傾向分析（活動全体の傾向・偏り・進捗）

## 参照する情報の優先順位（上ほど信頼して優先）
1. 長期記憶（ユーザー本人の就活プロフィール）
2. 企業研究メモ
3. OCRメモ（紙資料の取り込み）
4. お気に入り企業
5. AI要約・分類結果
6. 過去の会話（必要な場合のみ）

## 回答のルール
- 下に渡される情報に書かれた事実のみを根拠にしてください。
- **事実と推測を明確に区別してください。**
  - メモや長期記憶に明記された内容は「事実」として述べる。
  - あなたの解釈・提案・可能性は「推測」「提案」と分かる書き方にする
    （例:「〜と考えられます」「〜がおすすめです（推測）」）。
- 渡された情報に無い固有情報（企業名・日程・結果など）は推測で断定しないでください。
  情報が足りない場合は「メモにこの情報がない」と正直に伝え、何を記録すればよいか促してください。
- 一般的な就活アドバイス（考え方・進め方）は、情報が少なくても述べて構いません。
- 回答は日本語で、実行しやすいよう具体的・簡潔に。箇条書きを活用してください。
- どの情報を根拠にしたかが分かるよう、本文中で企業名・種別・長期記憶の項目に触れてください。
"""


def _format_context(retrieved: list[RetrievedMemo]) -> str:
    """検索で選ばれたメモ群を、Gemini に渡すコンテキスト文字列へ整形する。

    全メモではなく、関連する少数のメモだけを渡す（RAG の要）。
    """
    if not retrieved:
        return "（関連する保存済みメモは見つかりませんでした。）"

    blocks: list[str] = []
    for i, r in enumerate(retrieved, start=1):
        m = r.memo
        created = ""
        try:
            created = m.created_at.astimezone().strftime("%Y-%m-%d")
        except Exception:
            created = str(m.created_at)[:10]

        # sections を「キー: 値」の短い箇条書きに
        sec_lines = ""
        if m.sections:
            sec_lines = "\n".join(f"  - {k}: {v}" for k, v in m.sections.items())

        kw = "、".join(m.keywords) if m.keywords else ""

        block = (
            f"[メモ{i}] 企業名: {m.company_name} / 種別: {m.memo_type} / 日付: {created}\n"
            + (f"キーワード: {kw}\n" if kw else "")
            + (f"要点:\n{sec_lines}\n" if sec_lines else "")
            + (f"次回アクション: {m.next_action}\n" if m.next_action else "")
        )
        blocks.append(block.rstrip())
    return "\n\n".join(blocks)


# ─── Gemini クライアント（エージェント用・遅延初期化） ───────────────────────

_agent_model: genai.GenerativeModel | None = None


def _get_agent_model() -> genai.GenerativeModel:
    """エージェント用の Gemini モデルを取得する（既存の設定を再利用）。"""
    global _agent_model
    if _agent_model is None:
        if not settings.gemini_api_key:
            raise AIAnalysisError(
                title="🔑 APIキーが設定されていません",
                guidance=(
                    "`GEMINI_API_KEY` が未設定です。\n"
                    "・プロジェクトルートの `.env` に `GEMINI_API_KEY=AIza...` を設定してください。\n"
                    "・設定後はアプリを再起動してください。"
                ),
                kind="auth",
            )
        genai.configure(api_key=settings.gemini_api_key)
        _agent_model = genai.GenerativeModel(
            model_name=settings.gemini_model,   # 既存のモデル設定を再利用
            generation_config=genai.GenerationConfig(
                response_mime_type="text/plain",
                temperature=0.4,
                max_output_tokens=4096,
            ),
        )
    return _agent_model


# ─── 単一の窓口 ───────────────────────────────────────────────────────────────

def ask_agent(
    question: str,
    memos: list[MemoEntry],
    *,
    long_term_lines: list[str] | None = None,
    favorites: list[str] | None = None,
    history: list[dict] | None = None,
    top_k: int = 6,
    retriever: Retriever | None = None,
) -> AgentResponse:
    """就活AIエージェントに質問する（優先順位付き RAG）。

    参照優先順位:
        1. 長期記憶 → 2. 企業研究メモ → 3. OCRメモ → 4. お気に入り企業
        → 5. AI要約・分類結果 → 6. 過去の会話（必要な場合のみ）

    Args:
        question: ユーザーの自由記述の質問。
        memos:    全保存済みメモ（この中から関連分だけを検索して使う）。
        long_term_lines: 長期記憶を「項目: 値」に整形した行リスト（最優先の参照）。
        favorites: お気に入り企業名のリスト。
        history:  直近の会話履歴（必要な場合のみ渡す。全会話は渡さない）。
        top_k:    Gemini に渡す関連メモの最大件数。
        retriever: 使用する検索器（省略時は既定 = KeywordRetriever）。

    Returns:
        AgentResponse（回答 + 参照メモ + 参考情報の由来一覧）。

    Raises:
        AIAnalysisError: Gemini API 呼び出しが失敗した場合（原因別に分類）。
    """
    retriever = retriever or get_retriever()
    long_term_lines = long_term_lines or []
    favorites = favorites or []

    # 1. 関連メモだけを検索（全件は送らない = RAG の核心）
    retrieved = retriever.retrieve(question, memos, top_k=top_k)
    logger.info(
        "[Agent] retrieve: query_len=%d 全%d件中 %d件を選択",
        len(question), len(memos), len(retrieved),
    )

    # 透明性: どの情報を参照したかを記録（UI で「参考情報」として提示）
    info_sources: list[str] = []

    # ── 優先順位に沿ってコンテキストを構築 ──────────────────────────────
    context_parts: list[str] = []

    # 1. 長期記憶（最優先）
    if long_term_lines:
        context_parts.append(
            "## 【優先度1】長期記憶（ユーザーの就活プロフィール）\n"
            + "\n".join(f"- {ln}" for ln in long_term_lines)
        )
        for ln in long_term_lines:
            info_sources.append(f"長期記憶：{ln}")

    # 2〜3,5. 関連メモを種別で優先度づけして提示（企業研究 > OCR > その他）
    if retrieved:
        # OCR メモ（source_type=="ocr"）を区別
        research = [r for r in retrieved if r.memo.memo_type == "企業研究"]
        ocr = [r for r in retrieved if getattr(r.memo, "source_type", "") == "ocr"]
        others = [r for r in retrieved if r not in research and r not in ocr]

        if research:
            context_parts.append(
                "## 【優先度2】企業研究メモ\n" + _format_context(research)
            )
        if ocr:
            context_parts.append(
                "## 【優先度3】OCRメモ（紙資料の取り込み）\n" + _format_context(ocr)
            )
        if others:
            context_parts.append(
                "## 【優先度5】その他の関連メモ（AI要約・分類結果）\n" + _format_context(others)
            )
        # 参考情報（メモの由来）
        for r in retrieved:
            m = r.memo
            src = "OCRメモ" if getattr(m, "source_type", "") == "ocr" else f"{m.memo_type}メモ"
            info_sources.append(f"{m.company_name}（{src}）")

    # 4. お気に入り企業
    if favorites:
        context_parts.append(
            "## 【優先度4】お気に入り企業\n" + "、".join(favorites)
        )
        info_sources.append("お気に入り企業：" + "、".join(favorites))

    context = "\n\n".join(context_parts) if context_parts else "（関連する保存済み情報は見つかりませんでした。）"

    # 6. 会話履歴（必要な場合のみ・直近数ターン）
    history_text = ""
    if history:
        recent = history[-6:]
        lines = []
        for h in recent:
            role = "ユーザー" if h.get("role") == "user" else "エージェント"
            lines.append(f"{role}: {h.get('content', '')}")
        history_text = "## 【優先度6】これまでの会話（参考）\n" + "\n".join(lines) + "\n\n"

    prompt = (
        _AGENT_SYSTEM_PROMPT
        + "\n\n---\n\n"
        + history_text
        + "## 参照情報（この事実のみを根拠にし、事実と推測を区別する）\n"
        + context
        + "\n\n---\n\n"
        + f"## ユーザーの質問\n{question}\n\n"
        + "上記を踏まえ、就活AIエージェントとして、事実と推測を区別して回答してください。"
    )

    # Gemini 呼び出し
    try:
        response = _get_agent_model().generate_content(prompt)
        answer = (response.text or "").strip()
    except AIAnalysisError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("[Agent] Gemini 呼び出し失敗:\n%s", traceback.format_exc())
        raise _classify_api_error(e) from e

    if not answer:
        answer = "うまく回答を生成できませんでした。質問を少し具体的にして再度お試しください。"

    return AgentResponse(answer=answer, references=retrieved, info_sources=info_sources)
