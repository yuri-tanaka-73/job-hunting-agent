"""
お気に入り機能サービス

設計方針:
- まずは Streamlit の session_state を保存先とする（サーバー再起動で消える揮発性）。
- 将来 Google スプレッドシートへ永続保存できるよう、保存処理を
  「バックエンド（FavoriteStore）」として抽象化し、差し替え可能にする。
- UI 層（app.py）は FavoritesManager だけに依存させ、保存先の実装詳細を隠蔽する。

切り替え方法（将来）:
    SheetsFavoriteStore を実装し、FavoritesManager(store=SheetsFavoriteStore(...))
    のように渡すだけで永続化に切り替えられる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


# ─── データモデル ─────────────────────────────────────────────────────────────

@dataclass
class FavoriteItem:
    """お気に入り1件。保存項目: 企業名 / インターン名 / 登録日時。"""

    company_name: str
    intern_name: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def key(self) -> str:
        """重複判定に使う一意キー（企業名＋インターン名の組み合わせ）。"""
        return _make_key(self.company_name, self.intern_name)


def _make_key(company_name: str, intern_name: str = "") -> str:
    """企業名・インターン名から重複判定用キーを生成する（前後空白と大小無視）。"""
    c = (company_name or "").strip().lower()
    i = (intern_name or "").strip().lower()
    return f"{c}\u0000{i}"


# ─── 保存バックエンド（差し替え可能な抽象） ──────────────────────────────────

class FavoriteStore(Protocol):
    """お気に入りの永続化バックエンドが満たすインターフェース。

    session_state 版・Sheets 版など、実装を差し替えられるようにする。
    """

    def load(self) -> list[FavoriteItem]:
        """保存済みのお気に入りを全件読み込む。"""
        ...

    def save(self, items: list[FavoriteItem]) -> None:
        """お気に入り全件を保存する（上書き）。"""
        ...


class SessionStateFavoriteStore:
    """Streamlit の session_state を保存先とするバックエンド（揮発性・既定）。"""

    def __init__(self, state_key: str = "favorites") -> None:
        self._state_key = state_key

    def _get_state(self):
        # import をメソッド内に閉じ込め、streamlit 非依存のテストを可能にする
        import streamlit as st
        return st.session_state

    def load(self) -> list[FavoriteItem]:
        state = self._get_state()
        return list(state.get(self._state_key, []))

    def save(self, items: list[FavoriteItem]) -> None:
        state = self._get_state()
        state[self._state_key] = list(items)


class InMemoryFavoriteStore:
    """テスト用のインメモリバックエンド（streamlit 非依存）。"""

    def __init__(self) -> None:
        self._items: list[FavoriteItem] = []

    def load(self) -> list[FavoriteItem]:
        return list(self._items)

    def save(self, items: list[FavoriteItem]) -> None:
        self._items = list(items)


# ─── マネージャ ───────────────────────────────────────────────────────────────

class FavoritesManager:
    """お気に入りの追加・解除・一覧・重複防止を担うマネージャ。

    保存先は FavoriteStore（既定は session_state）に委譲する。
    UI 層はこのクラスのメソッドだけを使う。
    """

    def __init__(self, store: FavoriteStore | None = None) -> None:
        # 既定は session_state バックエンド。将来は Sheets 版を渡すだけで切替可能。
        self._store: FavoriteStore = store or SessionStateFavoriteStore()

    # ── 参照系 ────────────────────────────────────────────────────────────

    def list_all(self) -> list[FavoriteItem]:
        """お気に入りを登録日時の新しい順で返す。"""
        items = self._store.load()
        return sorted(items, key=lambda x: x.created_at, reverse=True)

    def count(self) -> int:
        """お気に入り件数。"""
        return len(self._store.load())

    def is_favorite(self, company_name: str, intern_name: str = "") -> bool:
        """指定の企業名＋インターン名が既にお気に入り済みか。"""
        target = _make_key(company_name, intern_name)
        return any(it.key() == target for it in self._store.load())

    # ── 更新系 ────────────────────────────────────────────────────────────

    def add(self, company_name: str, intern_name: str = "") -> bool:
        """お気に入りに追加する。

        Returns:
            True: 追加した / False: 既に登録済み（重複のため追加せず）
        """
        company_name = (company_name or "").strip()
        intern_name = (intern_name or "").strip()
        if not company_name:
            return False
        items = self._store.load()
        target = _make_key(company_name, intern_name)
        # 重複登録防止
        if any(it.key() == target for it in items):
            return False
        items.append(FavoriteItem(company_name=company_name, intern_name=intern_name))
        self._store.save(items)
        return True

    def remove(self, company_name: str, intern_name: str = "") -> bool:
        """お気に入りを解除する。

        Returns:
            True: 解除した / False: 対象が存在しなかった
        """
        target = _make_key(company_name, intern_name)
        items = self._store.load()
        new_items = [it for it in items if it.key() != target]
        if len(new_items) == len(items):
            return False
        self._store.save(new_items)
        return True

    def toggle(self, company_name: str, intern_name: str = "") -> bool:
        """お気に入り状態を反転する。

        Returns:
            True: 追加された / False: 解除された
        """
        if self.is_favorite(company_name, intern_name):
            self.remove(company_name, intern_name)
            return False
        self.add(company_name, intern_name)
        return True

    def clear(self) -> None:
        """全お気に入りを削除する。"""
        self._store.save([])
