# 就活メモ整理AIエージェント システム設計書

**バージョン**: 1.0  
**作成日**: 2026-09-09  
**依拠文書**: [要件定義書](./requirements.md)  
**ステータス**: Draft

---

## 目次

1. [システム構成図](#1-システム構成図)
2. [技術スタック](#2-技術スタック)
3. [フォルダ構成](#3-フォルダ構成)
4. [画面構成](#4-画面構成)
5. [APIエンドポイント設計](#5-apiエンドポイント設計)
6. [Googleスプレッドシート連携](#6-googleスプレッドシート連携)
7. [AI要約・分類処理](#7-ai要約分類処理)
8. [自然言語検索処理](#8-自然言語検索処理)
9. [将来のOCR連携](#9-将来のocr連携)
10. [環境変数・設定管理](#10-環境変数設定管理)
11. [デプロイ構成](#11-デプロイ構成)
12. [シーケンス図](#12-シーケンス図)

---

## 1. システム構成図

### 1.1 全体構成（MVP）

```
┌─────────────────────────────────────────────────────────────┐
│                        ユーザー端末                           │
│                                                             │
│  ┌──────────────┐              ┌──────────────────────────┐  │
│  │  PC ブラウザ  │              │  スマホ ブラウザ           │  │
│  │  （登録・検索）│              │  （閲覧・検索）            │  │
│  └──────┬───────┘              └───────────┬──────────────┘  │
└─────────┼────────────────────────────────┼─────────────────┘
          │  HTTPS                          │  HTTPS
          ▼                                 ▼
┌─────────────────────────────────────────────────────────────┐
│                   Webアプリケーション                          │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              フロントエンド（Next.js）                    │  │
│  │  ・メモ登録フォーム    ・メモ一覧/詳細                    │  │
│  │  ・検索UI             ・レスポンシブ対応                  │  │
│  └──────────────────────────┬─────────────────────────────┘  │
│                             │ API呼び出し (HTTP/JSON)         │
│  ┌──────────────────────────▼─────────────────────────────┐  │
│  │              バックエンド（FastAPI）                      │  │
│  │                                                        │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │  │
│  │  │ メモ登録API  │  │  検索API      │  │  メモ取得API  │  │  │
│  │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │  │
│  │         │                │                   │          │  │
│  │  ┌──────▼──────┐  ┌──────▼───────┐           │          │  │
│  │  │  AIサービス  │  │  検索サービス │           │          │  │
│  │  │ (分類・要約) │  │  (キーワード/ │           │          │  │
│  │  └──────┬──────┘  │   ベクトル)   │           │          │  │
│  │         │          └──────────────┘           │          │  │
│  │  ┌──────▼──────────────────────────────────────▼──────┐  │  │
│  │  │            Sheetsサービス（読み書き）                 │  │  │
│  │  └──────────────────────────┬──────────────────────────┘  │  │
│  └─────────────────────────────┼──────────────────────────┘  │
└────────────────────────────────┼────────────────────────────┘
                                 │
          ┌──────────────────────┼───────────────────────┐
          │                      │                       │
          ▼                      ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  Google Sheets   │  │  OpenAI API      │  │  （将来）        │
│  API v4          │  │  GPT-4o          │  │  Google Vision   │
│  （永続ストレージ）│  │  Embeddings      │  │  API (OCR)       │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 1.2 データフロー（メモ登録時）

```
ユーザー入力（テキスト）
    │
    ▼
[バリデーション]
    │
    ▼
[AI処理] ──── OpenAI API
  ├─ 企業名推定
  ├─ カテゴリ分類
  └─ 要約生成（3〜5行）
    │
    ▼
[メモエントリ生成]（UUID付与・タイムスタンプ）
    │
    ▼
[Google Sheets 書き込み]
    │
    ▼
レスポンス返却 → フロントエンド表示
```

---

## 2. 技術スタック

| レイヤー | 採用技術 | バージョン | 理由 |
|----------|----------|------------|------|
| フロントエンド | Next.js | 14.x (App Router) | レスポンシブ対応・スマホ閲覧が容易 |
| バックエンド | FastAPI (Python) | 0.111.x | 非同期処理・型安全・OpenAPI自動生成 |
| AI | OpenAI API | GPT-4o-mini | コスト効率と精度のバランス |
| Embedding | OpenAI Embeddings | text-embedding-3-small | 将来の意味検索に備える |
| ストレージ | Google Sheets API | v4 | スマホからの直接閲覧・共有が容易 |
| 認証 | Googleサービスアカウント | - | 個人利用・サーバーサイド完結 |
| Python依存管理 | uv | 最新安定版 | 高速・lockfile管理 |
| ホスティング | Render (MVP) | - | 無料枠あり・デプロイ簡単 |

> **補足**: フロントエンドは Streamlit への切り替えも可能。その場合 `frontend/` ディレクトリは不要となり、`backend/` 内に Streamlit アプリを配置する。

---

## 3. フォルダ構成

```
job-hunting-agent/
├── docs/
│   ├── requirements.md          # 要件定義書
│   └── design.md                # 本設計書
│
├── backend/                     # FastAPI アプリケーション
│   ├── app/
│   │   ├── main.py              # FastAPI エントリーポイント
│   │   ├── config.py            # 設定・環境変数読み込み
│   │   │
│   │   ├── api/                 # ルーター（エンドポイント定義）
│   │   │   ├── __init__.py
│   │   │   ├── memos.py         # メモCRUD エンドポイント
│   │   │   └── search.py        # 検索エンドポイント
│   │   │
│   │   ├── services/            # ビジネスロジック
│   │   │   ├── __init__.py
│   │   │   ├── ai_service.py    # AI分類・要約処理
│   │   │   ├── sheets_service.py# Google Sheets 読み書き
│   │   │   ├── search_service.py# 検索ロジック
│   │   │   └── ocr_service.py   # OCR処理（将来拡張用stub）
│   │   │
│   │   ├── models/              # Pydantic データモデル
│   │   │   ├── __init__.py
│   │   │   └── memo.py          # MemoEntry スキーマ
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── helpers.py       # UUID生成・日付変換など
│   │
│   ├── tests/
│   │   ├── test_ai_service.py
│   │   ├── test_sheets_service.py
│   │   └── test_search_service.py
│   │
│   ├── pyproject.toml           # 依存関係定義 (uv)
│   ├── uv.lock
│   └── .env.example             # 環境変数テンプレート
│
├── frontend/                    # Next.js アプリケーション
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx       # 共通レイアウト
│   │   │   ├── page.tsx         # トップ（メモ一覧）
│   │   │   ├── new/
│   │   │   │   └── page.tsx     # メモ登録フォーム
│   │   │   ├── memos/
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx # メモ詳細
│   │   │   └── search/
│   │   │       └── page.tsx     # 検索結果
│   │   │
│   │   ├── components/
│   │   │   ├── MemoCard.tsx     # メモカードUI
│   │   │   ├── MemoForm.tsx     # 登録フォーム
│   │   │   ├── SearchBar.tsx    # 検索バー
│   │   │   └── FilterPanel.tsx  # 企業名・カテゴリフィルター
│   │   │
│   │   └── lib/
│   │       └── api.ts           # バックエンドAPIクライアント
│   │
│   ├── package.json
│   └── .env.local.example
│
├── .gitignore
└── README.md
```

---

## 4. 画面構成

### 4.1 画面一覧

| 画面ID | 画面名 | URL | 主な利用場面 |
|--------|--------|-----|-------------|
| S-01 | メモ一覧 | `/` | 登録済みメモの確認・フィルタリング |
| S-02 | メモ登録 | `/new` | テキストコピペ・手動入力で登録 |
| S-03 | メモ詳細 | `/memos/:id` | 要約・本文の確認と編集 |
| S-04 | 検索結果 | `/search?q=...` | 自然言語・キーワード検索結果 |
| S-05 | （将来）OCR登録 | `/new/ocr` | 画像アップロード・OCR確認 |

### 4.2 各画面レイアウト

#### S-01 メモ一覧

```
┌─────────────────────────────────────────┐
│  🔍 [検索バー              ] [登録 +]   │  ← ヘッダー
├─────────────────────────────────────────┤
│  フィルター: [企業名 ▼] [カテゴリ ▼]    │
├─────────────────────────────────────────┤
│  ┌───────────────────────────────────┐  │
│  │ 株式会社○○  |  面接  |  2026/09/08  │  │  ← MemoCard
│  │ ・技術面接では〇〇について聞かれた  │  │
│  │ ・逆質問は〇〇を準備すると良い     │  │
│  └───────────────────────────────────┘  │
│  ┌───────────────────────────────────┐  │
│  │ 株式会社△△  |  説明会 |  2026/09/05 │  │
│  │ ・事業内容は〇〇                   │  │
│  └───────────────────────────────────┘  │
│  ...                                    │
└─────────────────────────────────────────┘
```

#### S-02 メモ登録

```
┌─────────────────────────────────────────┐
│  ← 戻る    メモを登録                   │
├─────────────────────────────────────────┤
│  企業名（任意）:                         │
│  [                              ]       │
│                                         │
│  メモ本文 *:                             │
│  ┌─────────────────────────────────┐   │
│  │                                 │   │
│  │  ここにテキストをコピペ          │   │
│  │                                 │   │
│  └─────────────────────────────────┘   │
│                                         │
│  カテゴリ（任意・AIが推定）:              │
│  [自動検出          ▼]                  │
│                                         │
│         [  AIで分析して登録  ]           │
│                                         │
│  ※ 企業名・カテゴリはAIが自動設定します │
└─────────────────────────────────────────┘
```

#### S-03 メモ詳細

```
┌─────────────────────────────────────────┐
│  ← 戻る    株式会社○○ / 面接            │  ← ヘッダー
├─────────────────────────────────────────┤
│  📅 2026-09-08  🏷️ 一次面接, 技術        │
├─────────────────────────────────────────┤
│  📝 AIによる要約                  [編集] │
│  ─────────────────────────────────────  │
│  • 技術面接ではアルゴリズムが出題        │
│  • チーム開発経験について深掘りあり      │
│  • 逆質問で技術スタックを確認推奨        │
├─────────────────────────────────────────┤
│  📄 元のメモ                      [編集] │
│  ─────────────────────────────────────  │
│  （全文テキスト）                        │
└─────────────────────────────────────────┘
```

#### S-04 検索結果

```
┌─────────────────────────────────────────┐
│  🔍 [○○社の面接について       ] [検索]  │
├─────────────────────────────────────────┤
│  💬 AIの回答:                            │
│  ─────────────────────────────────────  │
│  ○○社の面接では技術面接と人物面接の     │
│  2回があります。技術では...              │
├─────────────────────────────────────────┤
│  📎 参照したメモ（3件）:                 │
│  ┌───────────────────────────────────┐  │
│  │ [面接メモ 2026/09/08]  > 詳細      │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

### 4.3 レスポンシブ対応方針

- ブレークポイント: スマホ `< 640px`、タブレット `640〜1024px`、PC `> 1024px`
- スマホでは1カラム表示、カードのテキストは要約のみ表示（全文は詳細画面）
- フォントサイズ最小 16px（スマホ入力時のズーム防止）

---

## 5. APIエンドポイント設計

ベースURL: `http://localhost:8000/api/v1`（本番は環境変数で切り替え）

### メモ系

| メソッド | パス | 説明 | リクエスト | レスポンス |
|----------|------|------|-----------|-----------|
| `POST` | `/memos` | メモ登録（AI処理含む） | `MemoCreateRequest` | `MemoEntry` |
| `GET` | `/memos` | メモ一覧取得 | `?company=&category=&limit=&offset=` | `MemoListResponse` |
| `GET` | `/memos/{id}` | メモ詳細取得 | - | `MemoEntry` |
| `PATCH` | `/memos/{id}` | メモ更新（要約・カテゴリ修正） | `MemoUpdateRequest` | `MemoEntry` |
| `DELETE` | `/memos/{id}` | メモ削除 | - | `204 No Content` |

### 検索系

| メソッド | パス | 説明 | リクエスト | レスポンス |
|----------|------|------|-----------|-----------|
| `POST` | `/search` | 自然言語検索 | `SearchRequest` | `SearchResponse` |

### リクエスト/レスポンス スキーマ

```python
# models/memo.py

class MemoCreateRequest(BaseModel):
    raw_text: str                          # 必須: 本文
    company_name: str | None = None        # 任意: 未指定でAI推定
    category: str | None = None            # 任意: 未指定でAI推定
    memo_date: date | None = None          # 任意: メモの日付

class MemoEntry(BaseModel):
    id: str                                # UUID
    created_at: datetime
    updated_at: datetime
    company_name: str
    category: str
    tags: list[str]
    source_type: Literal["paste", "ocr", "manual"]
    raw_text: str
    summary: str
    memo_date: date | None

class MemoUpdateRequest(BaseModel):
    company_name: str | None = None
    category: str | None = None
    summary: str | None = None
    tags: list[str] | None = None

class SearchRequest(BaseModel):
    query: str                             # 自然言語クエリ
    limit: int = 5

class SearchResponse(BaseModel):
    answer: str                            # AIによる回答文
    sources: list[MemoEntry]              # 参照したメモ一覧
```

---

## 6. Googleスプレッドシート連携

### 6.1 認証方式

サービスアカウント認証を採用する。ユーザーのブラウザを介さずサーバーサイドで完結するため、個人利用に最適。

```
手順:
1. Google Cloud Console でプロジェクトを作成
2. Google Sheets API を有効化
3. サービスアカウントを作成 → JSON鍵をダウンロード
4. 対象スプレッドシートをサービスアカウントのメールアドレスに「編集者」権限で共有
5. JSON鍵のパスを環境変数 GOOGLE_SERVICE_ACCOUNT_JSON に設定
```

### 6.2 スプレッドシート構造

**シート名**: `memos`  
**1行目**: ヘッダー行（固定）

| 列 | カラム名 | 型 | 説明 |
|----|----------|----|------|
| A | id | string | UUID（主キー） |
| B | created_at | string | ISO8601形式 |
| C | updated_at | string | ISO8601形式 |
| D | memo_date | string | YYYY-MM-DD |
| E | company_name | string | 企業名 |
| F | category | string | カテゴリ |
| G | tags | string | カンマ区切り |
| H | source_type | string | paste/ocr/manual |
| I | summary | string | AI要約（改行は\nエスケープ） |
| J | raw_text | string | 本文（改行は\nエスケープ） |

### 6.3 sheets_service.py 実装設計

```python
# services/sheets_service.py

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

class SheetsService:
    def __init__(self, spreadsheet_id: str, credentials_json: str):
        creds = service_account.Credentials.from_service_account_file(
            credentials_json, scopes=SCOPES
        )
        self.service = build("sheets", "v4", credentials=creds)
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = "memos"

    def append_memo(self, memo: MemoEntry) -> None:
        """メモを1行追加する"""
        values = [self._memo_to_row(memo)]
        body = {"values": values}
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A:J",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

    def get_all_memos(self) -> list[MemoEntry]:
        """全メモを取得する（2行目以降）"""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A2:J",
        ).execute()
        rows = result.get("values", [])
        return [self._row_to_memo(row) for row in rows if len(row) >= 10]

    def update_memo(self, memo: MemoEntry) -> None:
        """IDで行を検索して更新する"""
        row_index = self._find_row_by_id(memo.id)
        if row_index is None:
            raise ValueError(f"Memo not found: {memo.id}")
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A{row_index}:J{row_index}",
            valueInputOption="RAW",
            body={"values": [self._memo_to_row(memo)]},
        ).execute()

    def _find_row_by_id(self, memo_id: str) -> int | None:
        """IDに対応する行番号（1始まり）を返す"""
        all_ids = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A2:A",
        ).execute().get("values", [])
        for i, row in enumerate(all_ids, start=2):
            if row and row[0] == memo_id:
                return i
        return None

    @staticmethod
    def _memo_to_row(memo: MemoEntry) -> list:
        return [
            memo.id, memo.created_at.isoformat(), memo.updated_at.isoformat(),
            str(memo.memo_date) if memo.memo_date else "",
            memo.company_name, memo.category,
            ",".join(memo.tags), memo.source_type,
            memo.summary.replace("\n", "\\n"),
            memo.raw_text.replace("\n", "\\n"),
        ]

    @staticmethod
    def _row_to_memo(row: list) -> MemoEntry:
        return MemoEntry(
            id=row[0], created_at=row[1], updated_at=row[2],
            memo_date=row[3] or None, company_name=row[4],
            category=row[5], tags=row[6].split(",") if row[6] else [],
            source_type=row[7],
            summary=row[8].replace("\\n", "\n"),
            raw_text=row[9].replace("\\n", "\n"),
        )
```

### 6.4 API呼び出し上限への対応

Google Sheets API の無料枠は **300リクエスト/分**。個人利用（500件以下）では問題ないが、以下を実装する。

- 一覧取得はキャッシュ（TTL: 60秒）を設けてAPI呼び出しを削減
- 検索時はキャッシュから全件取得してメモリ上でフィルタリング

---

## 7. AI要約・分類処理

### 7.1 処理フロー

```
入力テキスト
    │
    ▼
[単一プロンプトでOpenAI APIを呼び出す]
    │  ※ 分類・企業名推定・要約を1回のAPI呼び出しで取得（コスト削減）
    ▼
[JSON形式でレスポンスを受け取る]
    │
    ▼
[パース・バリデーション]
    │
    ├─ 成功 → MemoEntry に格納
    └─ 失敗（JSON不正） → リトライ（最大2回） → デフォルト値でフォールバック
```

### 7.2 プロンプト設計

```python
# services/ai_service.py

SYSTEM_PROMPT = """
あなたは就職活動メモの整理AIです。
ユーザーが入力したメモテキストを分析し、以下のJSON形式で返答してください。

{
  "company_name": "企業名（不明な場合は '不明'）",
  "category": "以下のいずれか: 企業研究/説明会/OB訪問/ES・履歴書/面接/選考結果/その他",
  "tags": ["関連タグ1", "関連タグ2"],
  "summary": "3〜5行の箇条書き要約（日本語、各行を '・' で始める）"
}

注意:
- 企業名はメモ本文から抽出してください。固有名詞に注意してください。
- カテゴリは内容から最も適切なものを1つ選んでください。
- 要約は重要なポイントのみを抽出してください。
- JSON以外の文字を出力しないでください。
"""

async def analyze_memo(raw_text: str, company_hint: str | None, category_hint: str | None) -> AnalysisResult:
    user_content = raw_text
    if company_hint:
        user_content = f"企業名ヒント: {company_hint}\n\n{raw_text}"
    if category_hint:
        user_content = f"カテゴリヒント: {category_hint}\n\n{user_content}"

    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},  # JSON modeを使用
        temperature=0.2,  # 分類の一貫性のため低めに設定
        max_tokens=512,
    )
    return parse_analysis_result(response.choices[0].message.content)
```

### 7.3 コスト見積もり

| モデル | 入力トークン単価 | 出力トークン単価 | 1メモあたり概算コスト |
|--------|----------------|----------------|---------------------|
| gpt-4o-mini | $0.15/1M | $0.60/1M | 約500入力 + 200出力 = **$0.0002** |

500件登録しても約 **$0.10**（約15円）。実用上コストは無視できる水準。

---

## 8. 自然言語検索処理

### 8.1 MVP: キーワード検索 + AI回答生成

```
クエリ入力 ("○○社の面接について")
    │
    ▼
[全メモをSheetsキャッシュから取得]
    │
    ▼
[形態素解析なしのシンプルなキーワードマッチ]
  ・クエリを空白で分割しAND検索
  ・company_name / category / summary / raw_text を対象
    │
    ▼
[上位5件を抽出]
    │
    ▼
[OpenAI APIに検索結果 + クエリを渡して回答生成]
    │
    ▼
[回答文 + 参照メモ一覧を返却]
```

### 8.2 将来: ベクトル検索

データ件数が200件を超えたタイミングで切り替えを検討。

```
[登録時] raw_text → OpenAI Embeddings API → ベクトル保存（Sheetsの列Kに追記）

[検索時] クエリ → Embeddings → コサイン類似度でTop-K取得
```

ベクトルDBとして軽量な **ChromaDB**（ローカル）または **Pinecone**（クラウド）を採用予定。

### 8.3 検索プロンプト設計

```python
SEARCH_SYSTEM_PROMPT = """
あなたは就職活動のアドバイザーです。
以下のメモ一覧を参考に、ユーザーの質問に日本語で回答してください。
メモに記載されていない情報は回答しないでください。
回答は3〜5文で簡潔にまとめてください。
"""

def build_search_prompt(query: str, memos: list[MemoEntry]) -> str:
    memo_text = "\n---\n".join([
        f"[{m.company_name} / {m.category} / {m.memo_date}]\n{m.summary}"
        for m in memos
    ])
    return f"参考メモ:\n{memo_text}\n\n質問: {query}"
```

---

## 9. 将来のOCR連携

### 9.1 方針

MVPでは `ocr_service.py` をスタブとして実装し、将来の差し込みを容易にする。

```python
# services/ocr_service.py（MVP: スタブ）

class OcrService:
    def extract_text(self, image_bytes: bytes) -> str:
        raise NotImplementedError("OCR機能は将来実装予定です")
```

### 9.2 将来実装: Google Vision API 連携

```
[実装手順]
1. Google Cloud Console で Vision API を有効化
2. 同一サービスアカウントに Vision API の権限付与
3. ocr_service.py の実装を差し替え

[処理フロー]
スマホカメラ撮影
    │
    ▼
[画像アップロード API: POST /memos/ocr]
  ・multipart/form-data で画像受け取り
  ・ファイルサイズ上限: 5MB
  ・対応フォーマット: JPEG / PNG / WebP
    │
    ▼
[Google Vision API: TEXT_DETECTION]
  ・手書き文字は DOCUMENT_TEXT_DETECTION を使用
    │
    ▼
[OCRテキスト → フロントエンドへ返却]
  ・ユーザーが確認・修正
    │
    ▼
[通常のメモ登録フロー（AI分類・要約）へ]
```

### 9.3 将来実装: ocr_service.py 差し替え版

```python
# services/ocr_service.py（将来実装）

from google.cloud import vision

class OcrService:
    def __init__(self):
        self.client = vision.ImageAnnotatorClient()

    def extract_text(self, image_bytes: bytes) -> str:
        image = vision.Image(content=image_bytes)
        # 手書きメモには DOCUMENT_TEXT_DETECTION が高精度
        response = self.client.document_text_detection(image=image)
        if response.error.message:
            raise RuntimeError(f"Vision API error: {response.error.message}")
        return response.full_text_annotation.text
```

### 9.4 OCR用画面（S-05、将来拡張）

```
┌─────────────────────────────────────────┐
│  ← 戻る    写真でメモを登録              │
├─────────────────────────────────────────┤
│  ┌─────────────────────────────────┐   │
│  │                                 │   │
│  │   📷  写真を選択 / カメラ起動    │   │
│  │                                 │   │
│  └─────────────────────────────────┘   │
│                                         │
│  [読み取り結果の確認・修正テキストエリア]│
│  ┌─────────────────────────────────┐   │
│  │  OCRで読み取ったテキスト...      │   │
│  └─────────────────────────────────┘   │
│                                         │
│         [  このテキストで登録  ]         │
└─────────────────────────────────────────┘
```

---

## 10. 環境変数・設定管理

### 10.1 backend/.env.example

```bash
# OpenAI
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# Google Sheets
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service_account.json
GOOGLE_SPREADSHEET_ID=1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# アプリ設定
APP_ENV=development          # development | production
CORS_ORIGINS=http://localhost:3000
CACHE_TTL_SECONDS=60

# （将来）OCR
# GOOGLE_VISION_ENABLED=false
```

### 10.2 frontend/.env.local.example

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

### 10.3 秘密情報の管理方針

- `.env` / `service_account.json` は `.gitignore` に追加し、絶対にコミットしない
- 本番環境（Render等）ではホスティングサービスの環境変数管理機能を使用
- `service_account.json` はプロジェクトルート外（例: `~/.config/job-hunting-agent/`）に保管を推奨

---

## 11. デプロイ構成

### 11.1 ローカル開発環境

```bash
# バックエンド起動
cd backend
uv run uvicorn app.main:app --reload --port 8000

# フロントエンド起動
cd frontend
npm run dev  # → http://localhost:3000
```

### 11.2 本番（Render）

```
[Render: Web Service (backend)]
  ・Runtime: Python
  ・Build Command: pip install uv && uv sync
  ・Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ・環境変数: Renderのダッシュボードで設定

[Render: Static Site or Web Service (frontend)]
  ・Build Command: npm run build
  ・Publish Directory: .next
  ・環境変数: NEXT_PUBLIC_API_BASE_URL=https://your-backend.onrender.com/api/v1
```

---

## 12. シーケンス図

### 12.1 メモ登録シーケンス

```
ユーザー      フロントエンド    バックエンド    OpenAI API    Google Sheets
   │               │               │               │               │
   │ テキスト入力   │               │               │               │
   │──────────────>│               │               │               │
   │               │ POST /memos   │               │               │
   │               │──────────────>│               │               │
   │               │               │ analyze_memo  │               │
   │               │               │──────────────>│               │
   │               │               │ JSON結果      │               │
   │               │               │<──────────────│               │
   │               │               │               │  append_memo  │
   │               │               │───────────────────────────────>│
   │               │               │               │   200 OK      │
   │               │               │<───────────────────────────────│
   │               │  MemoEntry    │               │               │
   │               │<──────────────│               │               │
   │ 登録完了画面   │               │               │               │
   │<──────────────│               │               │               │
```

### 12.2 検索シーケンス

```
ユーザー      フロントエンド    バックエンド    OpenAI API    Sheetsキャッシュ
   │               │               │               │               │
   │ クエリ入力     │               │               │               │
   │──────────────>│               │               │               │
   │               │ POST /search  │               │               │
   │               │──────────────>│               │               │
   │               │               │ キャッシュ取得 │               │
   │               │               │──────────────────────────────>│
   │               │               │ 全メモ         │               │
   │               │               │<──────────────────────────────│
   │               │               │ キーワードフィルタ（メモリ内）  │
   │               │               │ 回答生成       │               │
   │               │               │──────────────>│               │
   │               │               │ 回答文         │               │
   │               │               │<──────────────│               │
   │               │ SearchResponse│               │               │
   │               │<──────────────│               │               │
   │ 検索結果表示   │               │               │               │
   │<──────────────│               │               │               │
```
