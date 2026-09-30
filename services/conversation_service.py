"""
会話履歴サービス（短期記憶）

AIエージェントとの会話を永続化する。session_state は再起動で消えるため、
面接練習を途中で終了しても再開できるよう、ローカル JSON ファイルに保存する。

保存先: data/conversations.json（.gitignore 済み = data/*.json）

機能:
- 会話の作成 / 追記 / 取得 / 一覧 / 削除 / 検索
- 面接練習の進行状況（例: 7/15問）の保持と再開

会話の種類（用途）:
- 面接練習 / ES添削 / 自己分析 / 企業研究相談 / 一般

設計:
- ストア（ConversationStore）を抽象化し、既定は JSON ファイル、テスト用に
  インメモリ実装を用意（streamlit 非依存でテスト可能）。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

# 会話の用途カテゴリ
CONVERSATION_TYPES = [
    "面接練習",
    "ES添削",
    "自己分析",
    "企業研究相談",
    "一般",
]


# ─── データモデル ─────────────────────────────────────────────────────────────

@dataclass
class Message:
    """会話1メッセージ。"""

    role: str            # "user" / "assistant"
    content: str
    refs: list = field(default_factory=list)   # 参考情報（表示用の軽量 dict のリスト）
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Conversation:
    """1つの会話セッション。"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "新しい会話"
    conv_type: str = "一般"
    messages: list = field(default_factory=list)   # list[Message]
    # 面接練習などの進行状況（例: {"current": 7, "total": 15}）。無ければ None。
    progress: dict | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── シリアライズ ──────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "conv_type": self.conv_type,
            "messages": [asdict(m) if isinstance(m, Message) else m for m in self.messages],
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "Conversation":
        msgs = []
        for m in d.get("messages", []):
            if isinstance(m, Message):
                msgs.append(m)
            else:
                msgs.append(Message(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    refs=m.get("refs", []) or [],
                    created_at=m.get("created_at", datetime.now(timezone.utc).isoformat()),
                ))
        return Conversation(
            id=d.get("id", str(uuid.uuid4())),
            title=d.get("title", "新しい会話"),
            conv_type=d.get("conv_type", "一般"),
            messages=msgs,
            progress=d.get("progress"),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=d.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )

    def progress_label(self) -> str:
        """進行状況の表示ラベル（例: '7/15問'）。無ければ空文字。"""
        if self.progress and "current" in self.progress and "total" in self.progress:
            return f"{self.progress['current']}/{self.progress['total']}問"
        return ""


# ─── ストア（永続化バックエンド） ────────────────────────────────────────────

class ConversationStore(Protocol):
    def load_all(self) -> list[Conversation]: ...
    def save_all(self, items: list[Conversation]) -> None: ...


class JsonConversationStore:
    """data/conversations.json に会話を保存する既定ストア。"""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            root = Path(__file__).resolve().parent.parent
            path = root / "data" / "conversations.json"
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[Conversation]:
        with self._lock:
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return [Conversation.from_dict(d) for d in raw]

    def save_all(self, items: list[Conversation]) -> None:
        data = [c.to_dict() for c in items]
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)   # アトミックに置き換え（破損防止）


class InMemoryConversationStore:
    """テスト用のインメモリストア（streamlit / ファイル非依存）。"""

    def __init__(self) -> None:
        self._items: list[Conversation] = []

    def load_all(self) -> list[Conversation]:
        return list(self._items)

    def save_all(self, items: list[Conversation]) -> None:
        self._items = list(items)


# ─── マネージャ ───────────────────────────────────────────────────────────────

class ConversationManager:
    """会話履歴の CRUD・検索・進行状況更新を担う。"""

    def __init__(self, store: ConversationStore | None = None) -> None:
        self._store: ConversationStore = store or JsonConversationStore()

    # ── 参照系 ────────────────────────────────────────────────────────────

    def list_all(self) -> list[Conversation]:
        """会話を更新日時の新しい順で返す。"""
        items = self._store.load_all()
        return sorted(items, key=lambda c: c.updated_at, reverse=True)

    def get(self, conv_id: str) -> Conversation | None:
        for c in self._store.load_all():
            if c.id == conv_id:
                return c
        return None

    def count(self) -> int:
        return len(self._store.load_all())

    def count_by_type(self, conv_type: str) -> int:
        return sum(1 for c in self._store.load_all() if c.conv_type == conv_type)

    def search(self, query: str) -> list[Conversation]:
        """タイトル・種別・本文を対象に部分一致で会話を検索する。"""
        q = (query or "").strip().lower()
        if not q:
            return self.list_all()
        hits = []
        for c in self.list_all():
            hay = " ".join([
                c.title, c.conv_type,
                " ".join(m.content if isinstance(m, Message) else m.get("content", "")
                         for m in c.messages),
            ]).lower()
            if q in hay:
                hits.append(c)
        return hits

    # ── 更新系 ────────────────────────────────────────────────────────────

    def create(self, title: str = "新しい会話", conv_type: str = "一般",
               progress: dict | None = None) -> Conversation:
        conv = Conversation(title=title, conv_type=conv_type, progress=progress)
        items = self._store.load_all()
        items.append(conv)
        self._store.save_all(items)
        return conv

    def save(self, conv: Conversation) -> None:
        """会話を保存（存在すれば更新、なければ追加）。updated_at を更新する。"""
        conv.updated_at = datetime.now(timezone.utc).isoformat()
        items = self._store.load_all()
        for i, c in enumerate(items):
            if c.id == conv.id:
                items[i] = conv
                self._store.save_all(items)
                return
        items.append(conv)
        self._store.save_all(items)

    def add_message(self, conv_id: str, role: str, content: str,
                    refs: list | None = None) -> Conversation | None:
        """会話にメッセージを追記して保存する。"""
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.messages.append(Message(role=role, content=content, refs=refs or []))
        self.save(conv)
        return conv

    def update_progress(self, conv_id: str, current: int, total: int) -> Conversation | None:
        """面接練習などの進行状況を更新する（例: 7/15問）。"""
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.progress = {"current": current, "total": total}
        self.save(conv)
        return conv

    def rename(self, conv_id: str, title: str) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        conv.title = title
        self.save(conv)
        return conv

    def delete(self, conv_id: str) -> bool:
        items = self._store.load_all()
        new_items = [c for c in items if c.id != conv_id]
        if len(new_items) == len(items):
            return False
        self._store.save_all(new_items)
        return True
