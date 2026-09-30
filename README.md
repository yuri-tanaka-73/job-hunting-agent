# 就活メモ整理AIエージェント

テキストを貼り付けると、AIが自動で要約・分類してGoogleスプレッドシートに保存します。

```
テキスト入力 → AI要約・分類（Google Gemini）→ Googleスプレッドシート保存 → 一覧表示
```

---

## セットアップ

### 1. 必要なもの

- Python 3.11 以上
- [uv](https://docs.astral.sh/uv/getting-started/installation/) （パッケージ管理）
- Google Gemini API キー
- Googleサービスアカウント + スプレッドシート

> **🔐 GitHub公開時の注意**
> このリポジトリには秘密情報（APIキー・サービスアカウント鍵）は含まれていません。
> 設定はすべて環境変数（`.env`）と `service_account.json` に外出しされており、
> これらは `.gitignore` によりコミット対象から除外されています。
> フォークやクローンで利用する場合は、下記手順で **自分の** 認証情報を設定してください。
> `.env` や `service_account.json` は**絶対にコミット・共有しないでください**。

---

### 2. Googleスプレッドシートの準備

#### 2-1. サービスアカウントの作成

1. [Google Cloud Console](https://console.cloud.google.com/) を開く
2. プロジェクトを作成（または既存を選択）
3. 「APIとサービス」→「ライブラリ」→ **Google Sheets API** を有効化
4. 「APIとサービス」→「認証情報」→「サービスアカウントを作成」
5. 作成後、「キー」タブ → 「鍵を追加」→「JSON」をダウンロード
6. ダウンロードしたファイルを `service_account.json` という名前でプロジェクトルートに配置

#### 2-2. スプレッドシートの準備

1. Googleスプレッドシートを新規作成
2. シート名を `memos` に変更（または新しいシートを `memos` という名前で追加）
3. URLの `/d/` と `/edit` の間の文字列をメモ（スプレッドシートID）
4. サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）に **編集者** 権限で共有

---

### 3. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集：

```env
GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json
GOOGLE_SPREADSHEET_ID=1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

### 4. 依存パッケージのインストール

```bash
# uv がない場合はインストール
# macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows:     powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv sync
```

---

### 5. アプリ起動

```bash
uv run streamlit run frontend/app.py
```

ブラウザで **http://localhost:8501** が自動的に開きます。

---

## 使い方

### メモの登録

1. テキストエリアにメモをコピペ（説明会メモ、面接の感想など何でも可）
2. 企業名・カテゴリは任意（空欄にするとAIが自動推定）
3. 「AIで分析して保存」ボタンをクリック
4. 10〜20秒でAIが要約・分類し、Googleスプレッドシートに保存されます

### 一覧の確認

- 登録したメモが新しい順で表示されます
- 企業名・カテゴリでフィルタリングできます
- カードをクリック → 「詳細」ボタンで全文と要約を確認できます

### スプレッドシートとの双方向同期

アプリとGoogleスプレッドシートは双方向で反映されます。

- **アプリ → スプレッドシート**: アプリ上での編集・削除は即座にスプレッドシートへ書き込まれます。
- **スプレッドシート → アプリ**: スプレッドシートを直接訂正した場合は、一覧画面の **「🔄 更新」** ボタンを押すと、キャッシュ（既定TTL60秒）を無視して最新内容を強制取得します。
- **自動更新（任意）**: 一覧画面の「自動更新」トグルをONにすると、選択した間隔（10〜120秒）ごとにスプレッドシートの最新を自動取得します。編集・削除の操作中は入力内容を守るため自動更新を一時停止します。

> メモ: 反映が遅い・古い内容が出る場合は、キャッシュTTL（`.env` の `CACHE_TTL_SECONDS`、既定60秒）内である可能性があります。「🔄 更新」を押すか自動更新をONにすると即時に最新化されます。

### スマホからの閲覧

同じネットワーク（同じWi-Fi）上のスマホから `http://<PCのIPアドレス>:8501` にアクセスすると閲覧できます。

- PCのIPアドレスは、Windows なら PowerShell で `ipconfig`、macOS/Linux なら `ifconfig` / `ip addr` で確認できます（例: `192.168.x.x`）。
- スマホからの接続を受け付けるには、アドレスを明示して起動すると確実です:

  ```bash
  uv run streamlit run frontend/app.py --server.address 0.0.0.0
  ```

- つながらない場合は、OSのファイアウォールでポート `8501` の受信を許可してください。

---

## API（任意・将来拡張）

このアプリの本体は Streamlit（`frontend/app.py`）です。通常の利用に API サーバーは不要です。

リポジトリには将来拡張用の FastAPI 実装（`app/`）が含まれています。API サーバーとして起動したい場合のみ、以下を実行します（任意）：

```bash
uv run uvicorn app.main:app --reload --port 8000
```

起動後、Swagger UI は http://localhost:8000/docs で確認できます。

| メソッド | パス | 説明 |
|----------|------|------|
| `POST` | `/api/v1/memos` | メモ登録（AI分析→Sheets保存） |
| `GET` | `/api/v1/memos` | メモ一覧取得 |
| `GET` | `/api/v1/memos/{id}` | メモ詳細取得 |
| `PATCH` | `/api/v1/memos/{id}` | メモ更新 |
| `DELETE` | `/api/v1/memos/{id}` | メモ削除 |
| `GET` | `/health` | サーバー死活確認 |

---

## プロジェクト構成

```
job-hunting-agent/
├── frontend/
│   └── app.py              # ★アプリ本体（Streamlit UI・OCR・一覧・エージェント）
├── config.py               # 設定・環境変数（ルート直下・全レイヤー共通）
├── models/
│   └── memo.py             # Pydantic データモデル
├── services/
│   ├── ai_service.py       # Google Gemini API（分類・要約・OCR）
│   ├── sheets_service.py   # Google Sheets API（読み書き）
│   ├── agent_service.py    # 就活AIエージェント（RAG）
│   ├── retriever_service.py# 関連メモ検索
│   ├── memory_service.py   # 長期記憶
│   ├── conversation_service.py # 会話履歴
│   └── favorites_service.py    # お気に入り
├── app/                    # （任意）将来拡張用の FastAPI 実装
│   ├── main.py             # FastAPI エントリーポイント
│   └── api/memos.py        # メモ CRUD エンドポイント
├── docs/
│   ├── requirements.md     # 要件定義書
│   └── design.md           # システム設計書
├── .streamlit/config.toml  # Streamlit 設定（LAN内スマホ接続の安定化など）
├── .env.example            # 環境変数テンプレート
├── service_account.json    # Google サービスアカウント鍵（各自で配置・非コミット）
├── pyproject.toml          # 依存関係
└── README.md               # このファイル
```

---

## トラブルシューティング

### `GOOGLE_SPREADSHEET_ID` が見つからない

`.env` ファイルに `GOOGLE_SPREADSHEET_ID` が設定されているか確認してください。

### `service_account.json` が見つからない

`.env` の `GOOGLE_SERVICE_ACCOUNT_JSON` のパスが正しいか確認してください。

### スプレッドシートへの書き込みエラー

サービスアカウントのメールアドレスにスプレッドシートの **編集者** 権限が付与されているか確認してください。

### AI分析が失敗する

`GEMINI_API_KEY` が正しく設定されているか確認してください。APIキーは `AIza` から始まります。

---

## コスト目安

本アプリは Google Gemini（Flash 系モデル）を利用します。個人利用の範囲であれば、
Gemini API の**無料枠**（分あたり・1日あたりのリクエスト上限）内で運用できることが多いです。

- 無料枠を超える場合や有料プランでの従量課金は、モデルと入出力トークン量によって変動します。
- 最新の料金・無料枠は公式ドキュメントをご確認ください: https://ai.google.dev/pricing

> メモ: 上限に達すると AI 分析時にエラーが表示されます（アプリ側で原因と再試行の目安を案内します）。

---

## ライセンス

MIT
