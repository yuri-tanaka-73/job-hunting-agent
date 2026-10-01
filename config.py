"""
アプリケーション設定（ルート直下・全レイヤー共通）

設計方針:
- 必須フィールドは Optional[str] + None デフォルトにして、モジュールロード時に
  ValidationError が起きないようにする（遅延バリデーション）
- 実際に値が必要になる箇所（サービス初期化時）で None チェックを行う
- Streamlit UI 側では check_settings() で未設定項目を検出して案内する

使い方:
    from config import settings, check_settings
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Gemini ───────────────────────────────────────────────────────────────
    # Optional にすることで .env が存在しなくてもモジュールロードが成功する
    gemini_api_key: Optional[str] = None
    # 動作確認済みモデル。gemini-1.5/2.5-flash は新規ユーザー提供終了のため使用不可。
    # 常に最新を追う場合は gemini-flash-latest も指定可能（バージョンは自動で変わる）。
    gemini_model: str = "gemini-3.6-flash"

    # ─── Google Sheets ────────────────────────────────────────────────────────
    # GOOGLE_SERVICE_ACCOUNT_JSON:
    #   - 環境変数に設定する場合は「鍵 JSON の中身そのもの」を指定する
    #     例: GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ...}'
    #   - 未設定（None）の場合は service_account.json ファイルへフォールバック
    #   - デフォルトを None にすることで「環境変数が明示設定されたか」を確実に判定できる
    google_service_account_json: Optional[str] = None
    google_spreadsheet_id: Optional[str] = None

    # ─── アプリ共通 ───────────────────────────────────────────────────────────
    cache_ttl_seconds: int = 60

    # ─── 設定状態チェック ────────────────────────────────────────────────────

    def missing_fields(self) -> list[str]:
        """未設定の必須項目名を返す（空リストなら設定完了）。

        チェック対象:
          - GEMINI_API_KEY            … 未設定なら AI 分析・OCR が使えない
          - GOOGLE_SPREADSHEET_ID     … 未設定なら保存・一覧ができない
          - GOOGLE_SERVICE_ACCOUNT_JSON … 環境変数の JSON 直書き、または
            service_account.json ファイルのどちらも無ければ Sheets 認証不可
        """
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.google_spreadsheet_id:
            missing.append("GOOGLE_SPREADSHEET_ID")
        if not self.service_account_available:
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        return missing

    def service_account_info(self) -> Optional[dict]:
        """環境変数 GOOGLE_SERVICE_ACCOUNT_JSON を JSON として解析して返す。

        - 環境変数が設定されていれば json.loads() で辞書化する。
        - 未設定（None）または JSON パース失敗の場合は None を返す。
        """
        raw = (self.google_service_account_json or "").strip()
        if not raw:
            return None
        import json
        try:
            info = json.loads(raw)
            return info if isinstance(info, dict) else None
        except (ValueError, TypeError):
            return None

    @property
    def service_account_file_exists(self) -> bool:
        """フォールバック用 service_account.json ファイルが存在するか。"""
        from pathlib import Path
        try:
            return Path("./service_account.json").expanduser().is_file()
        except Exception:
            return False

    @property
    def service_account_available(self) -> bool:
        """環境変数 JSON またはファイルのどちらかで認証可能か。

        優先順位:
          1. 環境変数 GOOGLE_SERVICE_ACCOUNT_JSON（JSON 文字列として解析）
          2. ./service_account.json ファイル
        """
        return self.service_account_info() is not None or self.service_account_file_exists

    # 後方互換のためのエイリアス
    @property
    def service_account_exists(self) -> bool:
        return self.service_account_available

    # 旧実装との互換（パス文字列を期待する箇所向け・使用禁止・削除予定）
    @property
    def service_account_file_path(self) -> Optional[str]:
        """後方互換用。JSON 直書きの場合は None を返す。"""
        if self.google_service_account_json is not None:
            return None   # 環境変数が設定されているのでファイルパスは不要
        return "./service_account.json"

    @property
    def is_ai_ready(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def is_sheets_ready(self) -> bool:
        return bool(self.google_spreadsheet_id) and self.service_account_available

    @property
    def spreadsheet_url(self) -> Optional[str]:
        """スプレッドシートをブラウザで開くための URL（ID 未設定なら None）"""
        if not self.google_spreadsheet_id:
            return None
        return f"https://docs.google.com/spreadsheets/d/{self.google_spreadsheet_id}/edit"

    @property
    def is_ready(self) -> bool:
        """全必須設定が揃っているか"""
        return self.is_ai_ready and self.is_sheets_ready


@lru_cache
def get_settings() -> Settings:
    """Settings のシングルトンを返す（モジュールロード時には呼ばれない）"""
    return Settings()


# モジュールレベルではインスタンス化しない。
# 各モジュールは get_settings() を呼び出して取得する。
# ただし利便性のため遅延プロパティとしてアクセスできる alias を用意する。
class _SettingsProxy:
    """
    `from config import settings` した後 `settings.gemini_api_key` のように
    使えるプロキシ。実際のインスタンス化は最初のアクセス時に行われる。
    """
    _instance: Settings | None = None

    def _get(self) -> Settings:
        if self._instance is None:
            self._instance = get_settings()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)

    def missing_fields(self) -> list[str]:
        return self._get().missing_fields()

    @property
    def is_ai_ready(self) -> bool:
        return self._get().is_ai_ready

    @property
    def is_sheets_ready(self) -> bool:
        return self._get().is_sheets_ready

    @property
    def service_account_exists(self) -> bool:
        return self._get().service_account_exists

    @property
    def service_account_available(self) -> bool:
        return self._get().service_account_available

    @property
    def service_account_file_path(self):
        return self._get().service_account_file_path

    @property
    def service_account_file_exists(self) -> bool:
        return self._get().service_account_file_exists

    def service_account_info(self):
        return self._get().service_account_info()

    @property
    def spreadsheet_url(self):
        return self._get().spreadsheet_url

    @property
    def is_ready(self) -> bool:
        return self._get().is_ready


settings: Settings = _SettingsProxy()  # type: ignore[assignment]
