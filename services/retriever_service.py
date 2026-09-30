"""
RAG 検索層（Retriever）

役割:
- 保存済みメモ（MemoEntry）の中から、ユーザーの質問に関連するものだけを選び出す。
- エージェントは「関連する少数のメモだけ」を Gemini に渡す（全件は送らない）。

設計方針（将来の件数増加に対応）:
- Retriever を抽象インターフェース（Protocol）にして実装を差し替え可能にする。
- まずは外部依存なしで動く軽量な「キーワード/TF スコアリング」実装を提供する
  （KeywordRetriever）。日本語も文字 n-gram でトークン化して部分一致に強くする。
- 将来データが増えたら、同じインターフェースを満たす埋め込みベクトル検索
  （例: Gemini text-embedding-004 + 近傍探索）に差し替えるだけでよい。

各メモは「検索用テキスト（company_name / memo_type / keywords / sections /
detailed_summary / raw_text を連結したもの）」に変換して索引する。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from models.memo import MemoEntry


# ─── 検索結果 ─────────────────────────────────────────────────────────────────

@dataclass
class RetrievedMemo:
    """検索でヒットしたメモ1件（関連スコア付き）。"""

    memo: MemoEntry
    score: float


# ─── トークナイザ（日本語対応の簡易版） ──────────────────────────────────────

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
# 日本語（漢字・ひらがな・カタカナ・長音）
_JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\u30fc]")


def _tokenize(text: str) -> list[str]:
    """テキストをトークン化する。

    - 英数字は単語単位（小文字化）
    - 日本語は 2-gram（bi-gram）に分解して部分一致に強くする
    これにより「面接」「志望動機」「株式会社○○」等の部分一致を拾える。
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []

    # 英数字の単語
    tokens.extend(_WORD_RE.findall(text))

    # 日本語は連続した日本語文字列を取り出し、2-gram にする
    jp_runs = re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\u30fc]+", text)
    for run in jp_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i:i + 2])
    return tokens


def _memo_to_search_text(memo: MemoEntry) -> str:
    """メモを検索用の1つのテキストにまとめる。"""
    parts: list[str] = [
        memo.company_name or "",
        memo.memo_type or "",
        " ".join(memo.keywords or []),
        " ".join(f"{k} {v}" for k, v in (memo.sections or {}).items()),
        memo.detailed_summary or "",
        memo.next_action or "",
        memo.raw_text or "",
    ]
    return "\n".join(p for p in parts if p)


# ─── Retriever インターフェース ──────────────────────────────────────────────

class Retriever(Protocol):
    """質問に関連するメモを返す検索器のインターフェース。

    実装を差し替え可能にする（キーワード版 / 将来の埋め込みベクトル版など）。
    """

    def retrieve(self, query: str, memos: list[MemoEntry], top_k: int = 6) -> list[RetrievedMemo]:
        """query に関連するメモを上位 top_k 件、スコア降順で返す。"""
        ...


# ─── キーワード / TF スコアリング実装（既定・外部依存なし） ───────────────────

class KeywordRetriever:
    """TF-IDF 風のスコアリングで関連メモを選ぶ軽量 Retriever。

    - 各メモを検索用テキストに変換してトークン化
    - クエリのトークンごとに、そのトークンを含むメモへ IDF 重み付きでスコア加算
    - 企業名の一致は追加ボーナス（「○○株式会社の面接対策」等の意図に効く）
    """

    def retrieve(self, query: str, memos: list[MemoEntry], top_k: int = 6) -> list[RetrievedMemo]:
        if not memos:
            return []
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            # クエリが空/記号のみ → 新しい順に top_k 件を返す（文脈として最近のメモ）
            recent = sorted(memos, key=lambda m: m.created_at, reverse=True)[:top_k]
            return [RetrievedMemo(memo=m, score=0.0) for m in recent]

        # 各メモのトークン集合と、全体の DF（文書頻度）を計算
        docs: list[tuple[MemoEntry, list[str], set[str]]] = []
        df: dict[str, int] = {}
        for m in memos:
            toks = _tokenize(_memo_to_search_text(m))
            tok_set = set(toks)
            docs.append((m, toks, tok_set))
            for t in tok_set:
                df[t] = df.get(t, 0) + 1

        n_docs = len(docs)
        query_lower = query.lower()

        results: list[RetrievedMemo] = []
        for memo, toks, tok_set in docs:
            if not toks:
                continue
            tf_total = len(toks)
            score = 0.0
            for qt in query_tokens:
                if qt in tok_set:
                    tf = toks.count(qt) / tf_total
                    idf = math.log((n_docs + 1) / (df.get(qt, 0) + 1)) + 1.0
                    score += tf * idf
            # 企業名がクエリに含まれていれば大きなボーナス
            if memo.company_name and memo.company_name != "不明":
                if memo.company_name.lower() in query_lower:
                    score += 5.0
            # メモ種別名がクエリに含まれていれば軽いボーナス
            if memo.memo_type and memo.memo_type.lower() in query_lower:
                score += 1.0

            if score > 0:
                results.append(RetrievedMemo(memo=memo, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        if results:
            return results[:top_k]

        # 一致なし → 新しい順に top_k 件（会話の足がかりとして最近のメモを渡す）
        recent = sorted(memos, key=lambda m: m.created_at, reverse=True)[:top_k]
        return [RetrievedMemo(memo=m, score=0.0) for m in recent]


# ─── 既定 Retriever の取得（将来ここを差し替えるだけで実装を切替） ────────────

_default_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    """既定の Retriever を返す。

    将来、埋め込みベクトル検索に切り替える場合はこの関数の戻り値を
    EmbeddingRetriever 等に変更するだけでよい（呼び出し側は変更不要）。
    """
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = KeywordRetriever()
    return _default_retriever
