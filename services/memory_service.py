"""
長期記憶サービス

会話全文ではなく、就活上重要な情報だけを抽出して永続化する。
これにより、毎回の会話を Gemini へ送らずとも、ユーザー像を踏まえた回答ができる。

保存項目（長期記憶）:
- 志望業界 / 志望職種 / 強み / 弱み / 価値観 / 就活軸 /
  興味企業 / 参加済みインターン / AIが発見した傾向

保存先: data/long_term_memory.json（.gitignore 済み = data/*.json）

更新方法:
- extract_and_update() が「既存の長期記憶」＋「新しい会話」を Gemini に渡し、
  重要情報のみを JSON で受け取ってマージ更新する（会話全文は保存しない）。
"""

from __future__ import annotations

import json
import logging
import threading
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai

from config import settings
from services.ai_service import AIAnalysisError, _classify_api_error

logger = logging.getLogger(__name__)


# ─── データモデル ─────────────────────────────────────────────────────────────

@dataclass
class LongTermMemory:
    """ユーザーの就活プロフィール（長期記憶）。

    リスト項目は重複なく蓄積する。単一値項目は最新で上書きする。
    """

    志望業界: list = field(default_factory=list)
    志望職種: list = field(default_factory=list)
    強み: list = field(default_factory=list)
    弱み: list = field(default_factory=list)
    価値観: list = field(default_factory=list)
    就活軸: list = field(default_factory=list)
    興味企業: list = field(default_factory=list)
    参加済みインターン: list = field(default_factory=list)
    傾向: list = field(default_factory=list)   # AIが発見した傾向
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LongTermMemory":
        m = LongTermMemory()
        for key in ("志望業界", "志望職種", "強み", "弱み", "価値観",
                    "就活軸", "興味企業", "参加済みインターン", "傾向"):
            val = d.get(key, [])
            if isinstance(val, str):
                val = [val] if val else []
            elif not isinstance(val, list):
                val = []
            setattr(m, key, [str(x).strip() for x in val if str(x).strip()])
        m.updated_at = d.get("updated_at", datetime.now(timezone.utc).isoformat())
        return m

    def is_empty(self) -> bool:
        return not any([
            self.志望業界, self.志望職種, self.強み, self.弱み, self.価値観,
            self.就活軸, self.興味企業, self.参加済みインターン, self.傾向,
        ])

    def as_context_lines(self) -> list[str]:
        """回答時のコンテキスト用に「項目: 値」の行リストへ整形する。"""
        lines = []
        mapping = {
            "志望業界": self.志望業界, "志望職種": self.志望職種,
            "強み": self.強み, "弱み": self.弱み, "価値観": self.価値観,
            "就活軸": self.就活軸, "興味企業": self.興味企業,
            "参加済みインターン": self.参加済みインターン, "傾向": self.傾向,
        }
        for k, v in mapping.items():
            if v:
                lines.append(f"{k}: {'、'.join(v)}")
        return lines


# ─── ストア ───────────────────────────────────────────────────────────────────

class JsonMemoryStore:
    """data/long_term_memory.json に保存する既定ストア。"""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            root = Path(__file__).resolve().parent.parent
            path = root / "data" / "long_term_memory.json"
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> LongTermMemory:
        with self._lock:
            if not self._path.exists():
                return LongTermMemory()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return LongTermMemory()
        return LongTermMemory.from_dict(raw)

    def save(self, mem: LongTermMemory) -> None:
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(mem.to_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self._path)


class InMemoryMemoryStore:
    """テスト用インメモリストア。"""

    def __init__(self) -> None:
        self._mem = LongTermMemory()

    def load(self) -> LongTermMemory:
        return self._mem

    def save(self, mem: LongTermMemory) -> None:
        self._mem = mem


# ─── Gemini 抽出プロンプト ────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
あなたは就活支援AIの記憶抽出器です。
以下の「これまでの長期記憶」と「新しい会話」を読み、就活上重要な情報だけを
抽出・統合して、更新後の長期記憶を JSON で返してください。

## ルール
- 会話の全文は保存しません。重要な事実・傾向だけを短い語句で抽出します。
- 各項目は文字列の配列。重複は避け、既存の値は保持しつつ新情報を追加します。
- 明確な根拠がない項目は無理に埋めず、既存のままにします（空配列可）。
- 「傾向」は会話やこれまでの情報から読み取れるユーザーの就活傾向を短くまとめます。
- JSON 以外は一切出力しないでください。

## 出力 JSON 形式（キーは固定）
{{
  "志望業界": [], "志望職種": [], "強み": [], "弱み": [], "価値観": [],
  "就活軸": [], "興味企業": [], "参加済みインターン": [], "傾向": []
}}

## これまでの長期記憶
{current}

## 新しい会話
{conversation}
"""

_extract_model: genai.GenerativeModel | None = None


def _get_extract_model() -> genai.GenerativeModel:
    global _extract_model
    if _extract_model is None:
        if not settings.gemini_api_key:
            raise AIAnalysisError(
                title="🔑 APIキーが設定されていません",
                guidance="`GEMINI_API_KEY` が未設定です。`.env` を設定してください。",
                kind="auth",
            )
        genai.configure(api_key=settings.gemini_api_key)
        _extract_model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )
    return _extract_model


def _merge_lists(existing: list, new: list) -> list:
    """重複を避けてリストをマージする（大小・前後空白を無視して判定）。"""
    result = list(existing)
    seen = {x.strip().lower() for x in existing}
    for item in new:
        s = str(item).strip()
        if s and s.lower() not in seen:
            result.append(s)
            seen.add(s.lower())
    return result


# ─── マネージャ ───────────────────────────────────────────────────────────────

class MemoryManager:
    """長期記憶の読み書きと、会話からの抽出更新を担う。"""

    def __init__(self, store=None) -> None:
        self._store = store or JsonMemoryStore()

    def load(self) -> LongTermMemory:
        return self._store.load()

    def save(self, mem: LongTermMemory) -> None:
        self._store.save(mem)

    def extract_and_update(self, conversation_text: str) -> LongTermMemory:
        """会話テキストを Gemini で分析し、重要情報のみ抽出して長期記憶を更新する。

        Raises:
            AIAnalysisError: Gemini 呼び出しに失敗した場合。
        """
        current = self.load()
        prompt = _EXTRACT_PROMPT.format(
            current=json.dumps(current.to_dict(), ensure_ascii=False),
            conversation=conversation_text[:8000],   # 長すぎる会話は上限で切る
        )
        try:
            resp = _get_extract_model().generate_content(prompt)
            data = json.loads(resp.text or "{}")
        except AIAnalysisError:
            raise
        except json.JSONDecodeError as e:
            logger.warning("[Memory] 抽出JSONのパース失敗: %s", e)
            return current   # 失敗時は既存のまま（壊さない）
        except Exception as e:  # noqa: BLE001
            logger.error("[Memory] 抽出失敗:\n%s", traceback.format_exc())
            raise _classify_api_error(e) from e

        # マージ更新（既存を保持しつつ新情報を追加）
        updated = LongTermMemory.from_dict(data)
        merged = LongTermMemory(
            志望業界=_merge_lists(current.志望業界, updated.志望業界),
            志望職種=_merge_lists(current.志望職種, updated.志望職種),
            強み=_merge_lists(current.強み, updated.強み),
            弱み=_merge_lists(current.弱み, updated.弱み),
            価値観=_merge_lists(current.価値観, updated.価値観),
            就活軸=_merge_lists(current.就活軸, updated.就活軸),
            興味企業=_merge_lists(current.興味企業, updated.興味企業),
            参加済みインターン=_merge_lists(current.参加済みインターン, updated.参加済みインターン),
            傾向=_merge_lists(current.傾向, updated.傾向),
        )
        merged.updated_at = datetime.now(timezone.utc).isoformat()
        self.save(merged)
        logger.info("[Memory] 長期記憶を更新しました")
        return merged
