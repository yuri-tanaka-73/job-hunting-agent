"""
Googleスプレッドシートサービス

シート別構成に対応:
・種別ごとに「シート名」と「セクション列」を SHEET_CONFIG で定義する
・SheetsService は初期化時にどの種別（＝どのシート・列構成）を扱うかを受け取る
・デフォルトは企業研究（DEFAULT_MEMO_TYPE）
・全件取得はインメモリキャッシュ（TTL: settings.cache_ttl_seconds）で高速化
"""

from __future__ import annotations

import logging
import ssl
import time
import traceback
from pathlib import Path

import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import settings
from models.memo import (
    MemoEntry,
    MEMO_TYPES,
    SHEET_CONFIG,
    DEFAULT_MEMO_TYPE,
    ALL_SECTION_COLUMNS,
)

logger = logging.getLogger(__name__)


def _normalize_memo_type(value: str, fallback: str) -> str:
    """
    Sheets から読み取った memo_type を現在の5分類に正規化する（後方互換）。
    - 旧 "ES" 表記 → "ES・履歴書"
    - 5分類に無い値（"その他"・"説明会" 等）→ fallback（そのシートの既定種別）
    """
    if not value:
        return fallback
    if value in ("ES", "履歴書"):
        return "ES・履歴書"
    if value in MEMO_TYPES:
        return value
    return fallback

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 共通列（全シート共通・固定）
_COMMON_COLUMNS = [
    "ID",
    "作成日時",
    "更新日時",
    "メモ日付",
    "企業名",
    "分類",
    "タグ",
    "入力方法",
    "メモ内容",
    "メモ種別",
    "キーワード",
]
_TAIL_COLUMNS = ["次回アクション"]


def _col_letter(index_zero_based: int) -> str:
    """0始まりの列番号を A1 記法の列文字（A, B, ..., Z, AA, ...）に変換する"""
    n = index_zero_based + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _quote_sheet(sheet_name: str) -> str:
    """
    シート名を A1 記法用に安全に引用符で囲む。

    Google Sheets の A1 記法では、シート名に英数字以外（日本語・記号・空白・
    "・" など）が含まれる場合、シングルクォートで囲む必要がある。
    シート名内のシングルクォートは '' にエスケープする。

    例:
        memos           -> 'memos'
        ES・履歴書       -> 'ES・履歴書'
        it's a sheet    -> 'it''s a sheet'
    """
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


# ─── サービスクラス ───────────────────────────────────────────────────────────

class SheetsService:
    def __init__(
        self,
        memo_type: str = DEFAULT_MEMO_TYPE,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
    ) -> None:
        """
        Args:
            memo_type: どの種別のシート構成を使うか（デフォルト: 企業研究）。
                       SHEET_CONFIG のキーを指定する。
        """
        # ── シート構成の決定 ──────────────────────────────────────────────
        config = SHEET_CONFIG.get(memo_type) or SHEET_CONFIG[DEFAULT_MEMO_TYPE]
        self._memo_type = memo_type
        self._sheet_name = config.sheet_name
        self._section_columns = list(config.sections)

        # 列レイアウト = 共通 + セクション + 次回アクション
        self._columns = _COMMON_COLUMNS + self._section_columns + _TAIL_COLUMNS
        self._n_common = len(_COMMON_COLUMNS)
        self._n_section = len(self._section_columns)
        self._n_total = len(self._columns)

        last_col = _col_letter(self._n_total - 1)
        # シート名は必ず引用符で囲む（日本語・"・" などを含むシート名対策）
        self._quoted_sheet = _quote_sheet(self._sheet_name)
        self._range = f"{self._quoted_sheet}!A:{last_col}"
        self._data_range = f"{self._quoted_sheet}!A2:{last_col}"
        self._header_range = f"{self._quoted_sheet}!A1:{last_col}1"
        self._append_range = f"{self._quoted_sheet}!A1"
        self._last_col = last_col

        self._spreadsheet_id = spreadsheet_id or settings.google_spreadsheet_id
        creds_path = Path(credentials_path or settings.google_service_account_json)

        logger.info("[Sheets] 接続開始 memo_type=%s sheet=%s 列数=%d",
                    memo_type, self._sheet_name, self._n_total)
        logger.info("[Sheets] spreadsheet_id = %s", self._spreadsheet_id)
        logger.info("[Sheets] credentials_path = %s", creds_path.resolve())

        if not creds_path.exists():
            msg = (
                f"service_account.json が見つかりません: {creds_path.resolve()}\n"
                f"ヒント: .env の GOOGLE_SERVICE_ACCOUNT_JSON を確認してください。"
            )
            logger.error("[Sheets] %s", msg)
            raise FileNotFoundError(msg)
        logger.info("[Sheets] service_account.json を確認しました ✓")

        try:
            creds = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=_SCOPES
            )
            self._creds = creds
            logger.info("[Sheets] 認証成功: %s", creds.service_account_email)
        except Exception:
            logger.error("[Sheets] 認証失敗:\n%s", traceback.format_exc())
            raise

        try:
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            self._sheets = self._service.spreadsheets()
            logger.info("[Sheets] API クライアント構築成功 ✓")
        except Exception:
            logger.error("[Sheets] API クライアント構築失敗:\n%s", traceback.format_exc())
            raise

        self._cache: list[MemoEntry] | None = None
        self._cache_at: float = 0.0
        self._header_cache: list[str] | None = None

        self._ensure_sheet_exists()
        self._ensure_header()

    # ─── スレッド安全な実行ヘルパー ────────────────────────────────────────────

    def _new_http(self) -> google_auth_httplib2.AuthorizedHttp:
        return google_auth_httplib2.AuthorizedHttp(self._creds, http=httplib2.Http())

    def _execute(self, request, *, op: str):
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                return request.execute(http=self._new_http())
            except (ssl.SSLError, ConnectionError, BrokenPipeError, OSError) as e:
                last_error = e
                logger.warning(
                    "[Sheets] %s: 接続エラー (attempt %d/2) 再試行\n%s",
                    op, attempt, traceback.format_exc(),
                )
                time.sleep(0.5 * attempt)
                continue
            except HttpError:
                logger.error("[Sheets] %s: HTTPエラー:\n%s", op, traceback.format_exc())
                raise
            except Exception:
                logger.error("[Sheets] %s: 予期しないエラー:\n%s", op, traceback.format_exc())
                raise
        logger.error("[Sheets] %s: リトライ後も失敗:\n%s", op, traceback.format_exc())
        raise last_error  # type: ignore[misc]

    # ─── 接続テスト ───────────────────────────────────────────────────────────

    def ping(self) -> dict:
        logger.info("[Sheets] ping 開始 (spreadsheet_id=%s)", self._spreadsheet_id)
        meta = self._execute(
            self._service.spreadsheets().get(spreadsheetId=self._spreadsheet_id),
            op="ping",
        )
        title = meta.get("properties", {}).get("title", "不明")
        sheet_names = [s["properties"]["title"] for s in meta.get("sheets", [])]
        logger.info("[Sheets] ping 成功 ✓  title=%s  sheets=%s", title, sheet_names)
        has_sheet = self._sheet_name in sheet_names
        return {
            "ok": True,
            "spreadsheet_title": title,
            "sheet_names": sheet_names,
            "has_memos_sheet": has_sheet,
            "target_sheet": self._sheet_name,
            "columns": self._columns,
        }

    # ─── 公開メソッド ─────────────────────────────────────────────────────────

    def append_memo(self, memo: MemoEntry) -> dict:
        """メモを末尾に1行追加し、追加を検証する。"""
        # 実ヘッダーに合わせて値を並べる（列順・列数がズレていても正しい列へ）。
        header = self._physical_header()
        row = self._memo_to_row(memo, header)
        expected_cols = len(header) if header else self._n_total
        logger.info("[Sheets] append 開始 id=%s 企業=%s", memo.id, memo.company_name)

        if len(row) != expected_cols:
            msg = f"列数不一致: 生成={len(row)} 期待={expected_cols}"
            logger.error("[Sheets] append 中止: %s", msg)
            raise ValueError(msg)

        rows_before = self._count_data_rows()
        logger.info("[Sheets] append 前の行数: %d", rows_before)

        resp = self._execute(
            self._sheets.values().append(
                spreadsheetId=self._spreadsheet_id,
                range=self._append_range,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ),
            op="append",
        )

        updates = resp.get("updates", {}) if isinstance(resp, dict) else {}
        updated_range = updates.get("updatedRange", "")
        updated_rows = int(updates.get("updatedRows", 0) or 0)
        logger.info(
            "[Sheets] append レスポンス: updatedRange=%s updatedRows=%s updatedColumns=%s",
            updated_range, updated_rows, updates.get("updatedColumns"),
        )

        if updated_rows < 1:
            msg = f"append が行を追加しませんでした（updatedRows={updated_rows}, range={updated_range!r}）。"
            logger.error("[Sheets] %s", msg)
            raise ValueError(msg)

        if updated_range and not updated_range.lstrip("'").startswith(self._sheet_name):
            logger.warning(
                "[Sheets] 追加先シートが想定外: updatedRange=%s（期待=%s）",
                updated_range, self._sheet_name,
            )

        self._invalidate_cache()
        rows_after = self._count_data_rows()
        logger.info("[Sheets] append 後の行数: %d（前=%d）", rows_after, rows_before)

        if rows_after <= rows_before:
            msg = f"append 後も行数が増えていません（前={rows_before} 後={rows_after}）。range={updated_range!r}"
            logger.error("[Sheets] %s", msg)
            raise ValueError(msg)

        logger.info("[Sheets] append 成功 ✓ id=%s range=%s", memo.id, updated_range)
        return {
            "updated_range": updated_range,
            "updated_rows": updated_rows,
            "rows_before": rows_before,
            "rows_after": rows_after,
        }

    def _count_data_rows(self) -> int:
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A2:A",
            ),
            op="_count_data_rows",
        )
        return len(result.get("values", []))

    def get_all_memos(self, force_refresh: bool = False) -> list[MemoEntry]:
        """
        シートの全メモを取得する。

        Args:
            force_refresh: True の場合、インメモリキャッシュ（TTL）を無視して
                必ずスプレッドシートから最新を読み直す。スプレッドシートを
                直接編集した内容をアプリへ即時反映したいときに使う。
        """
        if not force_refresh and self._is_cache_valid():
            return self._cache  # type: ignore[return-value]

        # 行1（実ヘッダー）も含めて読み取り、値は「列名ベース」で解釈する。
        # これにより、シートの列順が変わっていたり列が1つ増減していても
        # （例: インターンシートで "早期選考の案内" 列が無い等）、各値が正しい
        # 項目へマッピングされ、表示のズレを防げる。
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=self._range,   # A1 から（ヘッダー含む）
            ),
            op="get_all_memos",
        )
        values = result.get("values", [])
        header = values[0] if values else []
        data_rows = values[1:] if len(values) > 1 else []

        # 実ヘッダー → 列インデックスのマップ（列名ベース解釈に使う）。
        # ヘッダーが空、または期待レイアウトと完全一致する場合は従来どおり
        # 位置ベースで解釈する（header_map=None）。
        header_map: dict[str, int] | None = None
        if header and header != self._columns:
            header_map = {name: i for i, name in enumerate(header)}
            logger.info(
                "[Sheets] 実ヘッダーが期待レイアウトと異なるため列名ベースで解釈します "
                "(実列数=%d 期待列数=%d)", len(header), self._n_total,
            )

        memos = []
        for row in data_rows:
            try:
                memos.append(self._row_to_memo(row, header_map))
            except Exception as e:
                logger.warning("[Sheets] 不正な行をスキップ: %s | error: %s", row, e)
        self._cache = memos
        self._cache_at = time.monotonic()
        logger.info("[Sheets] get_all_memos: %d 件取得", len(memos))
        return memos

    def update_memo(self, memo: MemoEntry) -> None:
        row_index = self._find_row_index(memo.id)
        if row_index is None:
            raise ValueError(f"Memo not found: {memo.id}")
        # 実ヘッダーに合わせて値を並べ、書き込み範囲も実列数に合わせる。
        header = self._physical_header()
        row = self._memo_to_row(memo, header)
        last_col = _col_letter(len(header) - 1) if header else self._last_col
        self._execute(
            self._sheets.values().update(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A{row_index}:{last_col}{row_index}",
                valueInputOption="RAW",
                body={"values": [row]},
            ),
            op="update",
        )
        self._invalidate_cache()
        logger.info("[Sheets] update 成功 id=%s row=%d", memo.id, row_index)

    def delete_memo(self, memo_id: str) -> None:
        row_index = self._find_row_index(memo_id)
        if row_index is None:
            raise ValueError(f"Memo not found: {memo_id}")
        sheet_id = self._get_sheet_id()
        requests = [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_index - 1,
                    "endIndex": row_index,
                }
            }
        }]
        self._execute(
            self._sheets.batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={"requests": requests},
            ),
            op="delete",
        )
        self._invalidate_cache()
        logger.info("[Sheets] delete 成功 id=%s", memo_id)

    def update_header(self) -> None:
        """ヘッダー行を現在の列構成で上書きする。"""
        self._execute(
            self._sheets.values().update(
                spreadsheetId=self._spreadsheet_id,
                range=self._header_range,
                valueInputOption="RAW",
                body={"values": [self._columns]},
            ),
            op="update_header",
        )
        logger.info("[Sheets] ヘッダー行を更新しました: %s", self._columns)

    def migrate_layout(self) -> dict:
        """
        既存シートを現在の列構成へ安全に移行する。

        手順（列名ベースなので列順が変わっても壊れない）:
          1. 既存ヘッダーと全データ行を読む
          2. 既存ヘッダー名 → 値 のマッピングで各行を辞書化
          3. 新しい列構成の順序で値を並べ直す（新規列は空欄、消す列は捨てる）
          4. ヘッダー＋全データを新レイアウトで書き戻す

        企業研究データはすべて保持され、面接/ES/インターン専用列は取り除かれる。

        Returns:
            {"migrated_rows": int, "old_columns": [...], "new_columns": [...]}
        """
        logger.info("[Sheets] migrate_layout 開始 sheet=%s", self._sheet_name)

        # 1. 既存の全体（ヘッダー含む）を読む。旧レイアウトは列数が多いので広めに読む。
        wide_last = _col_letter(
            max(self._n_total, len(_COMMON_COLUMNS) + len(ALL_SECTION_COLUMNS) + 1) + 5
        )
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A1:{wide_last}",
            ),
            op="migrate.read",
        )
        values = result.get("values", [])
        if not values:
            logger.info("[Sheets] migrate: データなし。ヘッダーのみ作成します。")
            self.update_header()
            return {"migrated_rows": 0, "old_columns": [], "new_columns": self._columns}

        old_header = values[0]
        old_rows = values[1:]
        logger.info("[Sheets] migrate: 既存ヘッダー=%s 行数=%d", old_header, len(old_rows))

        # 2 & 3. 各行を「旧列名→値」の辞書にし、新レイアウト順に並べ直す
        new_matrix = [self._columns]  # 1行目 = 新ヘッダー
        for row in old_rows:
            row_dict = {}
            for i, col_name in enumerate(old_header):
                row_dict[col_name] = row[i] if i < len(row) else ""
            new_row = [row_dict.get(col_name, "") for col_name in self._columns]
            new_matrix.append(new_row)

        # 4. 既存領域をクリアしてから新レイアウトで書き戻す
        self._execute(
            self._sheets.values().clear(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A1:{wide_last}",
            ),
            op="migrate.clear",
        )
        self._execute(
            self._sheets.values().update(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A1",
                valueInputOption="RAW",
                body={"values": new_matrix},
            ),
            op="migrate.write",
        )
        self._invalidate_cache()
        logger.info(
            "[Sheets] migrate_layout 完了: %d 行を新レイアウト（%d列）へ移行",
            len(old_rows), self._n_total,
        )
        return {
            "migrated_rows": len(old_rows),
            "old_columns": old_header,
            "new_columns": self._columns,
        }

    # ─── プライベートメソッド ─────────────────────────────────────────────────

    def _ensure_sheet_exists(self) -> None:
        """対象シートがなければ作成する（将来の別シート追加に対応）。"""
        meta = self._execute(
            self._service.spreadsheets().get(spreadsheetId=self._spreadsheet_id),
            op="_ensure_sheet_exists.get",
        )
        names = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if self._sheet_name not in names:
            logger.info("[Sheets] シート '%s' が無いため作成します", self._sheet_name)
            self._execute(
                self._sheets.batchUpdate(
                    spreadsheetId=self._spreadsheet_id,
                    body={"requests": [{
                        "addSheet": {"properties": {"title": self._sheet_name}}
                    }]},
                ),
                op="_ensure_sheet_exists.add",
            )

    def _ensure_header(self) -> None:
        """ヘッダー行がなければ作成、旧構成なら現在の列構成で上書きする。"""
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=self._header_range,
            ),
            op="_ensure_header.get",
        )
        existing = result.get("values", [[]])[0] if result.get("values") else []

        if not existing:
            self.update_header()
            logger.info("[Sheets] ヘッダー行を新規作成しました")
        elif existing != self._columns:
            # 列名が違う（旧統合レイアウト等）→ 自動では上書きせず警告。
            # データ保全のため migrate_layout を促す。
            logger.warning(
                "[Sheets] ヘッダーが現在の構成と異なります。\n"
                "  既存: %s\n  期待: %s\n"
                "  migrate_layout() の実行を推奨（既存データを列名ベースで移行）。",
                existing, self._columns,
            )
        else:
            logger.info("[Sheets] ヘッダー行を確認しました ✓")

    def _physical_header(self, force: bool = False) -> list[str]:
        """シートの実ヘッダー行（1行目）を読み取り、キャッシュして返す。

        書き込み（append/update）時に、実際の列名順へ合わせて値を並べるために使う。
        """
        if not force and getattr(self, "_header_cache", None) is not None:
            return self._header_cache  # type: ignore[return-value]
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=self._header_range,
            ),
            op="_physical_header",
        )
        header = result.get("values", [[]])[0] if result.get("values") else []
        self._header_cache = header
        return header

    def _find_row_index(self, memo_id: str) -> int | None:
        result = self._execute(
            self._sheets.values().get(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._quoted_sheet}!A2:A",
            ),
            op="_find_row_index",
        )
        ids = result.get("values", [])
        for i, row in enumerate(ids, start=2):
            if row and row[0] == memo_id:
                return i
        return None

    def _get_sheet_id(self) -> int:
        meta = self._execute(
            self._service.spreadsheets().get(spreadsheetId=self._spreadsheet_id),
            op="_get_sheet_id",
        )
        for sheet in meta.get("sheets", []):
            if sheet["properties"]["title"] == self._sheet_name:
                return sheet["properties"]["sheetId"]
        raise ValueError(f"Sheet '{self._sheet_name}' not found")

    def _is_cache_valid(self) -> bool:
        return (
            self._cache is not None
            and (time.monotonic() - self._cache_at) < settings.cache_ttl_seconds
        )

    def _invalidate_cache(self) -> None:
        self._cache = None
        self._cache_at = 0.0

    def invalidate_cache(self) -> None:
        """インメモリキャッシュを外部から明示的に破棄する（公開API）。

        「🔄 更新」ボタン等で、次回の get_all_memos が必ずスプレッドシートを
        読み直すようにするために使う。
        """
        self._invalidate_cache()

    # ─── 変換ヘルパー（列レイアウトに依存するためインスタンスメソッド） ──────────

    def _memo_to_row(self, memo: MemoEntry, header: list[str] | None = None) -> list[str]:
        """MemoEntry → 1行。

        header（実ヘッダー）が渡され、期待レイアウトと異なる場合は、その列名
        順に合わせて値を並べる。これにより、シートの列順・列数が期待と違っても
        （例: "早期選考の案内" 列が無い等）正しい列へ書き込める。
        header が無い/期待どおりなら従来の固定レイアウトで並べる。
        """
        def esc(s: str) -> str:
            return (s or "").replace("\n", "\\n")

        # 項目名 → 値 の辞書を1度だけ構築（共通・セクション・末尾すべて）
        field_values: dict[str, str] = {
            "ID": memo.id,
            "作成日時": memo.created_at.isoformat(),
            "更新日時": memo.updated_at.isoformat(),
            "メモ日付": str(memo.memo_date) if memo.memo_date else "",
            "企業名": memo.company_name,
            "分類": memo.category,
            "タグ": ",".join(memo.tags),
            "入力方法": memo.source_type,
            "メモ内容": esc(memo.raw_text),
            "メモ種別": memo.memo_type,
            "キーワード": ",".join(memo.keywords),
            "次回アクション": esc(memo.next_action),
        }
        for name in self._section_columns:
            field_values[name] = esc(memo.sections.get(name, ""))

        if header and header != self._columns:
            # 実ヘッダーの列名順に並べる（未知の列名は空欄で埋める）
            return [field_values.get(name, "") for name in header]

        # 期待レイアウト（共通 + セクション + 次回アクション）
        return [field_values[name] for name in self._columns]

    def _row_to_memo(self, row: list, header_map: dict[str, int] | None = None) -> MemoEntry:
        """1行 → MemoEntry。

        header_map が渡された場合は「列名ベース」で値を取り出す（シートの実際の
        列順・列数に追従できる）。渡されない場合は従来どおり「位置ベース」で
        解釈する（ヘッダーが期待レイアウトと一致している通常ケース）。
        """
        def unesc(s: str) -> str:
            return (s or "").replace("\\n", "\n")

        if header_map is not None:
            # ── 列名ベース解釈 ────────────────────────────────────────────
            def by_name(name: str) -> str:
                idx = header_map.get(name)
                if idx is None or idx >= len(row):
                    return ""
                return row[idx] if row[idx] is not None else ""

            sections: dict[str, str] = {}
            for name in self._section_columns:
                val = unesc(by_name(name)).strip()
                if val:
                    sections[name] = val

            next_action = unesc(by_name("次回アクション"))
            detailed_summary = _sections_to_markdown(sections) if sections else ""

            return MemoEntry(
                id=by_name("ID"),
                created_at=by_name("作成日時"),
                updated_at=by_name("更新日時"),
                memo_date=by_name("メモ日付") or None,
                company_name=by_name("企業名") or "不明",
                category=by_name("分類") or "その他",
                tags=[t.strip() for t in by_name("タグ").split(",") if t.strip()],
                source_type=by_name("入力方法") or "paste",
                raw_text=unesc(by_name("メモ内容")),
                memo_type=_normalize_memo_type(by_name("メモ種別"), self._memo_type),
                keywords=[k.strip() for k in by_name("キーワード").split(",") if k.strip()],
                sections=sections,
                next_action=next_action,
                detailed_summary=detailed_summary,
            )

        # ── 位置ベース解釈（従来／ヘッダーが期待どおりの通常ケース） ──────────
        padded = list(row) + [""] * (self._n_total - len(row))

        sections = {}
        for i, name in enumerate(self._section_columns):
            val = unesc(padded[self._n_common + i]).strip()
            if val:
                sections[name] = val

        next_action = unesc(padded[self._n_common + self._n_section])
        detailed_summary = _sections_to_markdown(sections) if sections else ""

        return MemoEntry(
            id=padded[0],
            created_at=padded[1],
            updated_at=padded[2],
            memo_date=padded[3] if padded[3] else None,
            company_name=padded[4] or "不明",
            category=padded[5] or "その他",
            tags=[t.strip() for t in padded[6].split(",") if t.strip()],
            source_type=padded[7] or "paste",
            raw_text=unesc(padded[8]),
            memo_type=_normalize_memo_type(padded[9], self._memo_type),
            keywords=[k.strip() for k in padded[10].split(",") if k.strip()],
            sections=sections,
            next_action=next_action,
            detailed_summary=detailed_summary,
        )


# ─── シングルトン ─────────────────────────────────────────────────────────────

_service_instance: SheetsService | None = None


def get_sheets_service() -> SheetsService:
    global _service_instance
    if _service_instance is None:
        _service_instance = SheetsService()
    return _service_instance


# ─── モジュール関数（表示用ヘルパー） ─────────────────────────────────────────

def _sections_to_markdown(sections: dict[str, str]) -> str:
    """sections 辞書 → 見出し付き Markdown（Sheets 読み戻し時の表示用）"""
    blocks = []
    for name, value in sections.items():
        items = [v.strip() for v in value.replace("、", "\n").splitlines() if v.strip()]
        body = "\n".join(f"・{it}" for it in items) if items else "・（記載なし）"
        blocks.append(f"## {name}\n{body}")
    return "\n\n".join(blocks)
