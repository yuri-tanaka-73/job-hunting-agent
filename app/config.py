"""
FastAPI 用の設定モジュール

services/ や models/ からは直接このモジュールを参照しないこと。
それらは共通設定として ルート直下の config.py を使用する。

app/ 内のコード（main.py, api/ 等）は従来通りここから import できる:
    from app.config import settings, get_app_settings
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from config import Settings  # ルート直下の共通設定を継承


class AppSettings(Settings):
    """FastAPI 固有の設定を追加したサブクラス"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:5500"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_app_settings() -> AppSettings:
    return AppSettings()


class _AppSettingsProxy:
    _instance: AppSettings | None = None

    def _get(self) -> AppSettings:
        if self._instance is None:
            self._instance = get_app_settings()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


# `from app.config import settings` で参照できるプロキシ
settings: AppSettings = _AppSettingsProxy()  # type: ignore[assignment]
