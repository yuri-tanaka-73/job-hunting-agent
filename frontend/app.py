"""
就活メモ整理AIエージェント - Streamlit アプリ

起動:
    uv run streamlit run frontend/app.py

機能:
    1. メモ入力（テキストコピペ）
    2. AI による自動分類・要約（Google Gemini）
    3. Google スプレッドシートへの保存
    4. 保存済みメモの一覧表示・フィルタリング・詳細確認
"""

from __future__ import annotations

import logging
import sys
from io import BytesIO
from pathlib import Path

import streamlit as st

# ─── パス解決 ────────────────────────────────────────────────────────────────
# frontend/app.py はプロジェクトルートの外（frontend/）にあるため、
# ルートを sys.path に追加してモジュールを解決する。
# insert(0) ではなく append を使い、標準ライブラリや既存パスの優先度を下げない。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from config import settings
from models.memo import MemoEntry, MEMO_TYPES, SECTIONS_BY_TYPE, SHEET_CONFIG
from services.ai_service import analyze_memo, AIAnalysisError, _classify_api_error, extract_text_from_image
from services.sheets_service import SheetsService
from services.favorites_service import FavoritesManager
from services.agent_service import ask_agent, AgentResponse
from services.conversation_service import ConversationManager, CONVERSATION_TYPES
from services.memory_service import MemoryManager

logger = logging.getLogger(__name__)

# ─── ページ設定 ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="就活メモ整理",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── カスタムCSS ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
.memo-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.memo-card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
    flex-wrap: wrap;
}
.company-name {
    font-size: 1.05rem;
    font-weight: 700;
    color: #1e293b;
}
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    background: #f0fdf4;
    color: #166534;
}
.date-text {
    font-size: 0.78rem;
    color: #64748b;
    margin-left: auto;
}
.summary-text {
    font-size: 0.9rem;
    color: #334155;
    border-left: 3px solid #bfdbfe;
    padding-left: 10px;
    margin: 8px 0;
    line-height: 1.7;
    white-space: pre-line;
}
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.tag {
    font-size: 0.73rem;
    background: #f1f5f9;
    color: #64748b;
    padding: 2px 8px;
    border-radius: 999px;
}
hr.section { border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }

/* セットアップ案内カード */
.setup-step {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #2563eb;
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 12px;
    font-size: 0.92rem;
}
.setup-step code {
    background: #1e293b;
    color: #e2e8f0;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 0.88em;
}

/* ─── モバイル下部ナビゲーション（既定は非表示。モバイル時のみ表示） ───────── */
.mobile-nav-spacer { display: none; }   /* 下部ナビ分の余白（モバイル時のみ有効） */

/* ─── レスポンシブ最適化（画面幅768px以下をモバイルとして扱う） ───────────── */
@media (max-width: 768px) {
    /* 余白をやや狭くして表示領域を広げる */
    .block-container {
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
        padding-top: 1rem !important;
    }

    /* Streamlit の columns（横並び）をスマホでは縦並びにする。
       st.columns は横並びの flex 行を作るため、flex 方向を縦に変える。 */
    div[data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
        gap: 0.4rem !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
    }

    /* メモ登録の入力欄をスマホでは大きく表示する（タップしやすく） */
    .stTextArea textarea {
        font-size: 1rem !important;
        min-height: 180px !important;
    }
    .stTextInput input,
    .stSelectbox div[data-baseweb="select"] {
        font-size: 1rem !important;
    }
    /* ボタンもタップしやすい高さに */
    .stButton button, .stLinkButton a, .stFormSubmitButton button {
        min-height: 44px !important;
    }

    /* カード一覧は横スクロールを絶対に発生させない */
    .memo-card {
        padding: 12px 12px;              /* 余白を狭める */
        overflow-wrap: anywhere;         /* 長い語も折り返す */
        word-break: break-word;
    }
    .summary-text {
        font-size: 0.86rem;
        overflow-wrap: anywhere;
        word-break: break-word;
    }
    .company-name { font-size: 1rem; }
    /* 詳細画面の見出しサイズをスマホ向けに最適化 */
    .memo-card h1, .stMarkdown h1 { font-size: 1.35rem !important; }
    .memo-card h2, .stMarkdown h2 { font-size: 1.15rem !important; }
    .memo-card h3, .stMarkdown h3 { font-size: 1.02rem !important; }

    /* 横スクロール防止（ページ全体） */
    .main .block-container { overflow-x: hidden !important; }

    /* 下部ナビを表示し、コンテンツが隠れないよう余白を確保 */
    .mobile-nav-spacer { display: block; height: 72px; }
}

/* モバイル下部ナビ本体（Streamlit の下部固定バー）。
   PC では @media で非表示にし、モバイル時のみ表示する。 */
.mobile-bottom-nav { display: none; }
@media (max-width: 768px) {
    .mobile-bottom-nav {
        display: flex;
        position: fixed;
        bottom: 0; left: 0; right: 0;
        z-index: 9999;
        background: #ffffff;
        border-top: 1px solid #e2e8f0;
        box-shadow: 0 -2px 8px rgba(0,0,0,.06);
        justify-content: space-around;
        padding: 6px 4px calc(6px + env(safe-area-inset-bottom));
    }
    .mobile-bottom-nav .nav-item {
        flex: 1;
        text-align: center;
        font-size: 0.62rem;
        color: #64748b;
        text-decoration: none;
        line-height: 1.3;
    }
    .mobile-bottom-nav .nav-item .nav-icon { font-size: 1.15rem; display: block; }
    .mobile-bottom-nav .nav-item.active { color: #2563eb; font-weight: 700; }
}
</style>
""", unsafe_allow_html=True)


# ─── 設定チェック ─────────────────────────────────────────────────────────────

def check_settings() -> tuple[bool, list[str]]:
    """
    環境変数の設定状態を確認する。

    Returns:
        (is_ready, missing_fields)
        - is_ready: 全必須項目が設定されているか
        - missing_fields: 未設定の項目名リスト
    """
    missing = settings.missing_fields()
    return len(missing) == 0, missing


def show_setup_guide(missing: list[str]) -> None:
    """
    未設定の環境変数を具体的な手順で案内する画面を表示する。
    設定完了後にページをリロードするよう促す。
    """
    st.title("⚙️ セットアップが必要です")
    st.markdown(
        "アプリを使用するには、以下の環境変数を設定した `.env` ファイルが必要です。"
        "**設定は2ステップで完了します。**"
    )

    st.divider()

    # ── ステップ1: .env ファイルの作成 ──────────────────────────────────────
    st.subheader("Step 1　`.env` ファイルを作成する")
    st.markdown(
        "プロジェクトルート（`README.md` と同じ場所）に `.env` ファイルを作成し、"
        "以下の内容を記述してください。"
    )
    env_lines = ["# .env"]
    for field in missing:
        if field == "GEMINI_API_KEY":
            env_lines.append("GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxx  # ← あなたのキーに書き換える")
        elif field == "GOOGLE_SPREADSHEET_ID":
            env_lines.append("GOOGLE_SPREADSHEET_ID=1xxxxxxxxxxxxxxxxxxxx  # ← スプレッドシートIDに書き換える")
    env_lines.append("# ↓ ローカル: 鍵JSONファイルのパス / Render等: 鍵JSONの中身を直接貼り付け")
    env_lines.append("GOOGLE_SERVICE_ACCOUNT_JSON=./service_account.json")
    st.code("\n".join(env_lines), language="bash")

    st.divider()

    # ── ステップ2: 各項目の取得方法 ─────────────────────────────────────────
    st.subheader("Step 2　各項目の取得方法")

    for field in missing:
        if field == "GEMINI_API_KEY":
            st.markdown("""
<div class="setup-step">
<strong>🔑 GEMINI_API_KEY の取得</strong><br><br>
1. <a href="https://aistudio.google.com/app/apikey" target="_blank">https://aistudio.google.com/app/apikey</a> を開く<br>
2. 「APIキーを作成」をクリック（Googleアカウントでログイン）<br>
3. 生成された <code>AIza...</code> から始まるキーをコピー<br>
4. <code>.env</code> の <code>GEMINI_API_KEY=</code> の後に貼り付ける<br><br>
💡 無料枠あり（1分あたり15リクエスト、1日1500リクエスト）
</div>
""", unsafe_allow_html=True)

        elif field == "GOOGLE_SPREADSHEET_ID":
            st.markdown("""
<div class="setup-step">
<strong>📊 GOOGLE_SPREADSHEET_ID の取得</strong><br><br>
1. <a href="https://sheets.google.com" target="_blank">Google スプレッドシート</a> で新規シートを作成<br>
2. シート名を <code>memos</code> に変更（タブ右クリック → 名前を変更）<br>
3. URLの <code>/d/</code> と <code>/edit</code> の間の文字列がIDです：<br>
&nbsp;&nbsp;&nbsp;<code>https://docs.google.com/spreadsheets/d/<strong>【ここがID】</strong>/edit</code><br>
4. <code>.env</code> の <code>GOOGLE_SPREADSHEET_ID=</code> の後に貼り付ける
</div>
""", unsafe_allow_html=True)

            st.markdown("""
<div class="setup-step">
<strong>🔐 Google サービスアカウントの設定（スプレッドシートへのアクセス権）</strong><br><br>
1. <a href="https://console.cloud.google.com/" target="_blank">Google Cloud Console</a> でプロジェクトを作成<br>
2. 「APIとサービス」→「ライブラリ」→ <code>Google Sheets API</code> を有効化<br>
3. 「APIとサービス」→「認証情報」→「サービスアカウントを作成」<br>
4. 作成後、「キー」タブ → 「鍵を追加」→「JSON」でダウンロード<br>
5. ダウンロードした JSON ファイルを <code>service_account.json</code> という名前でプロジェクトルートに配置<br>
6. スプレッドシートの「共有」でサービスアカウントのメールアドレスに <strong>編集者</strong> 権限を付与
</div>
""", unsafe_allow_html=True)

    # ── サービスアカウント JSON が見つからない場合の専用案内 ──────────────
    # GEMINI/ID は設定済みでも service_account.json だけ無いケースを確実に案内する。
    if "GOOGLE_SERVICE_ACCOUNT_JSON" in missing and "GOOGLE_SPREADSHEET_ID" not in missing:
        st.markdown(f"""
<div class="setup-step">
<strong>🔐 サービスアカウント認証情報が見つかりません</strong><br><br>
<strong>ローカル環境の場合（ファイルを使う）：</strong><br>
1. <a href="https://console.cloud.google.com/" target="_blank">Google Cloud Console</a> で <code>Google Sheets API</code> を有効化<br>
2. サービスアカウントを作成し、「キー」→「JSON」でキーをダウンロード<br>
3. ダウンロードした JSON を <code>service_account.json</code> としてプロジェクトルートに配置<br>
（別パスにする場合は <code>.env</code> の <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> にパスを設定）<br>
4. スプレッドシートの「共有」でサービスアカウントのメールに <strong>編集者</strong> 権限を付与<br><br>
<strong>Render などのデプロイ環境の場合（ファイルを置かない）：</strong><br>
環境変数 <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> に、鍵 JSON の<strong>中身そのもの</strong>を設定します：<br>
<code>GOOGLE_SERVICE_ACCOUNT_JSON={{"type":"service_account", ...}}</code>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ── ステップ3: 再起動案内 ──────────────────────────────────────────────
    st.subheader("Step 3　アプリを再起動する")
    st.markdown("`.env` ファイルを保存したら、ターミナルでアプリを再起動してください：")
    st.code("uv run streamlit run frontend/app.py", language="bash")

    st.info(
        "💡 **ヒント**: `.env` ファイルを更新しても、Streamlit は自動で再読み込みしません。"
        "ターミナルで `Ctrl+C` を押して停止し、再度起動してください。"
    )

    # ── 現在の設定状態サマリー ────────────────────────────────────────────
    st.divider()
    st.subheader("📋 設定状態")
    col1, col2, col3 = st.columns(3)
    with col1:
        if settings.is_ai_ready:
            st.success("✅ GEMINI_API_KEY　設定済み")
        else:
            st.error("❌ GEMINI_API_KEY　**未設定**")
    with col2:
        if settings.google_spreadsheet_id:
            st.success("✅ GOOGLE_SPREADSHEET_ID　設定済み")
        else:
            st.error("❌ GOOGLE_SPREADSHEET_ID　**未設定**")
    with col3:
        if settings.service_account_exists:
            st.success("✅ service_account.json　配置済み")
        else:
            st.error("❌ service_account.json　**未配置**")

    env_path = ROOT / ".env"
    if env_path.exists():
        st.caption(f"`.env` ファイルの場所: `{env_path}`　（ファイルは存在しますが、上記の項目が未設定です）")
    else:
        st.caption(f"`.env` ファイルが見つかりません。`{env_path}` に作成してください。")


# ─── Sheets サービスの初期化 ──────────────────────────────────────────────────

@st.cache_resource(show_spinner="Googleスプレッドシートに接続中...")
def _init_sheets(memo_type: str = "企業研究") -> SheetsService:
    """
    指定した種別（シート構成）の SheetsService を初期化して返す。
    memo_type ごとに @st.cache_resource でキャッシュされる。
    """
    return SheetsService(memo_type=memo_type)


def get_sheets(memo_type: str = "企業研究") -> tuple[SheetsService | None, Exception | None]:
    """
    指定種別の SheetsService を取得する。

    Returns:
        (service, None)  接続成功時
        (None, exception) 接続失敗時
    """
    try:
        return _init_sheets(memo_type), None
    except Exception as e:
        return None, e


def get_all_memos_all_sheets(
    force_refresh: bool = False,
) -> tuple[list[MemoEntry], dict[str, int], list[tuple[str, Exception]]]:
    """
    5分類すべてのシート（memos / ES・履歴書 / 面接 / インターン / 選考結果）から
    メモを取得し、1つのリストに集約して返す。

    各シートは独立した SheetsService（memo_type ごと）で読み取る。あるシートの
    取得に失敗しても、他のシートの結果は返す（部分的成功を許容）。

    Args:
        force_refresh: True の場合、各シートの TTL キャッシュを無視して
            スプレッドシートから最新を読み直す。スプレッドシート側で直接
            訂正した内容をアプリへ即時反映したいとき（「🔄 更新」）に使う。

    Returns:
        (memos, counts, errors)
        - memos:  全シートのメモを created_at 降順で並べた統合リスト
        - counts: 種別名 → 取得件数 の辞書（0件シートも含む）
        - errors: 取得に失敗した (種別名, 例外) のリスト
    """
    all_memos: list[MemoEntry] = []
    counts: dict[str, int] = {}
    errors: list[tuple[str, Exception]] = []

    for memo_type in SHEET_CONFIG.keys():
        service, err = get_sheets(memo_type)
        if service is None:
            counts[memo_type] = 0
            if err is not None:
                errors.append((memo_type, err))
            continue
        try:
            memos = service.get_all_memos(force_refresh=force_refresh)
            counts[memo_type] = len(memos)
            all_memos.extend(memos)
        except Exception as e:  # noqa: BLE001 - 1シートの失敗で全体を止めない
            counts[memo_type] = 0
            errors.append((memo_type, e))

    all_memos.sort(key=lambda m: m.created_at, reverse=True)
    return all_memos, counts, errors


def get_sheets_for_memo(memo: MemoEntry) -> tuple[SheetsService | None, Exception | None]:
    """
    メモが属するシート（memo_type に対応）の SheetsService を返す。
    編集・削除を必ず正しいシートに対して行うために使う。
    """
    target_type = memo.memo_type if memo.memo_type in SHEET_CONFIG else "企業研究"
    return get_sheets(target_type)


# ─── ヘルパー ─────────────────────────────────────────────────────────────────

# ── お気に入り（session_state バックエンド。将来 Sheets へ切替可能） ──────────
def get_favorites_manager() -> FavoritesManager:
    """FavoritesManager を取得する（保存先は既定で session_state）。"""
    return FavoritesManager()


# ── ページ管理（モバイル下部ナビ / PC でも共通のページ切替） ──────────────────
PAGES = {
    "home": ("🏠", "ホーム"),
    "agent": ("🤖", "AIエージェント"),
    "favorites": ("⭐", "お気に入り"),
    "memo": ("📝", "メモ"),
    "analytics": ("📊", "分析"),
}


def get_current_page() -> str:
    """現在のページを返す。

    ページ遷移は「ユーザーの明示的な操作」でのみ発生させる。
    そのため、URL の ?page= は次の場合だけ採用する:
      1. セッション最初の描画（未初期化）… リロード/ブックマーク/モバイルアンカー
         からの着地を反映する。
      2. 前回の描画から ?page= の値が変化したとき … モバイル下部ナビの
         アンカー（?page=xxx）クリックなど、明示的なナビゲーション操作。

    一方で、絞り込み・キーワード検索などの通常の widget 操作による再実行では、
    URL の ?page= は（前回と）変化しないため採用せず、現在の
    session_state["current_page"] を維持する。これにより「検索したら分析画面へ
    飛ぶ」といった意図しない遷移を防ぐ。
    """
    try:
        qp_page = st.query_params.get("page")
    except Exception:
        qp_page = None
    if qp_page not in PAGES:
        qp_page = None

    initialized = "current_page" in st.session_state
    last_qp_page = st.session_state.get("_last_qp_page")

    # 明示的なナビゲーション操作の判定:
    #   - 初回描画（セッション未初期化）で URL に page があれば着地先として採用
    #   - URL の page が前回描画時と変わった（=アンカーがクリックされた）なら採用
    if qp_page is not None and (not initialized or qp_page != last_qp_page):
        st.session_state["current_page"] = qp_page

    resolved = st.session_state.get("current_page", "home")

    # 次回の「変化検出」の基準として、今回 URL に反映する page 値（=解決済みページ）
    # を記録する。main() 側で URL の ?page= を resolved に同期するため、通常の
    # widget 再実行では qp_page == resolved == _last_qp_page となり、URL 由来の
    # 上書きは発生しない（=検索・絞り込みで遷移しない）。
    st.session_state["_last_qp_page"] = resolved

    return resolved


def render_mobile_bottom_nav(active: str) -> None:
    """モバイル用の下部ナビゲーションを描画する（CSS によりモバイル時のみ表示）。

    Streamlit のボタンは固定配置できないため、クエリパラメータ遷移する
    アンカー（<a href="?page=...">）を HTML で描画する。
    """
    items_html = ""
    for key, (icon, label) in PAGES.items():
        cls = "nav-item active" if key == active else "nav-item"
        items_html += (
            f'<a class="{cls}" href="?page={key}" target="_self">'
            f'<span class="nav-icon">{icon}</span>{label}</a>'
        )
    st.markdown(
        f'<div class="mobile-nav-spacer"></div>'
        f'<div class="mobile-bottom-nav">{items_html}</div>',
        unsafe_allow_html=True,
    )


def render_page_selector(active: str) -> None:
    """PC / 全環境共通のページ切替（横並びボタン）。

    下部ナビはモバイル専用のため、PC でもページを切り替えられるよう
    画面上部にボタン列を置く。モバイルでは CSS により縦並びになる。
    """
    cols = st.columns(len(PAGES))
    for col, (key, (icon, label)) in zip(cols, PAGES.items()):
        with col:
            btn_type = "primary" if key == active else "secondary"
            if st.button(f"{icon} {label}", key=f"nav_{key}",
                         use_container_width=True, type=btn_type):
                st.session_state["current_page"] = key
                # クエリパラメータも同期しておく（下部ナビとの整合）
                try:
                    st.query_params["page"] = key
                except Exception:
                    pass
                st.rerun()


def _clear_memo_caches() -> None:
    """メモ一覧のキャッシュを2層とも破棄する。

    1. Streamlit セッションの一覧キャッシュ（memos_cache::*）
    2. 各シート SheetsService の TTL インメモリキャッシュ

    これにより、スプレッドシートを直接訂正した内容が、TTL の残り時間に
    関係なく次回取得で必ず反映される。
    """
    # 1. セッション側の集約キャッシュ
    for key in [k for k in st.session_state.keys() if str(k).startswith("memos_cache")]:
        del st.session_state[key]

    # 2. 各シートサービス側の TTL キャッシュ（接続済みのものだけ）
    for memo_type in SHEET_CONFIG.keys():
        service, _ = get_sheets(memo_type)
        if service is not None:
            service.invalidate_cache()


def format_date(dt) -> str:
    try:
        return dt.astimezone().strftime("%Y/%m/%d")
    except Exception:
        return str(dt)[:10]


def category_color(category: str) -> str:
    return {
        "企業研究":   "#dbeafe",
        "説明会":     "#f0fdf4",
        "OB訪問":     "#fef9c3",
        "ES・履歴書": "#fce7f3",
        "面接":       "#ede9fe",
        "選考結果":   "#fee2e2",
        "その他":     "#f1f5f9",
    }.get(category, "#f1f5f9")


def memo_type_color(memo_type: str) -> tuple[str, str]:
    """メモ種別ごとの (背景色, 文字色) を返す（5分類）"""
    return {
        "企業研究":   ("#dbeafe", "#1e40af"),
        "ES・履歴書": ("#fce7f3", "#9d174d"),
        "面接":       ("#ede9fe", "#5b21b6"),
        "インターン": ("#fef9c3", "#854d0e"),
        "選考結果":   ("#dcfce7", "#166534"),
    }.get(memo_type, ("#f1f5f9", "#475569"))


def _card_summary(memo: MemoEntry) -> str:
    """一覧カード用の短い要約。sections の先頭数項目を短縮して1行にまとめる。

    要約文はカードでは短縮表示し、全文は「詳細を見る」で確認できる。
    """
    if memo.sections:
        parts = []
        for name, value in list(memo.sections.items())[:3]:
            # 各項目値を短縮（長い値で横スクロールや冗長表示にならないように）
            short_val = value if len(value) <= 24 else value[:24] + "…"
            parts.append(f"{name}：{short_val}")
        summary = "／".join(parts)
        # 全体もカード用に上限を設ける
        return summary if len(summary) <= 90 else summary[:90] + "…"
    # フォールバック（旧データ）
    return memo.detailed_summary.replace("\n", " ")[:80] or "（要約なし）"


def render_memo_card(memo: MemoEntry) -> None:
    tags_html = "".join(f'<span class="tag">{t}</span>' for t in memo.keywords)
    tag_section = f'<div class="tag-list">{tags_html}</div>' if memo.keywords else ""
    type_bg, type_fg = memo_type_color(memo.memo_type)

    display_summary = _card_summary(memo)

    st.markdown(f"""
    <div class="memo-card">
      <div class="memo-card-header">
        <span class="company-name">{memo.company_name}</span>
        <span class="badge" style="background:{type_bg};color:{type_fg}">
          {memo.memo_type}
        </span>
        <span class="date-text">{format_date(memo.created_at)}</span>
      </div>
      <div class="summary-text">{display_summary}</div>
      {tag_section}
    </div>
    """, unsafe_allow_html=True)


# ─── 画像処理（camera_input / file_uploader 共通） ───────────────────────────

# 自動リサイズの上限（長辺）。iPhone の写真は 3000〜4000px と大きく、
# そのまま Gemini に送るとタイムアウトや失敗の原因になるため縮小する。
_MAX_IMAGE_LONG_EDGE = 2000


def _guess_mime_from_format(pil_format: str | None) -> str:
    """PIL の画像フォーマット名から MIME タイプを推定する。"""
    fmt = (pil_format or "").upper()
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "HEIC": "image/heic",
        "HEIF": "image/heif",
        "GIF": "image/gif",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
    }.get(fmt, "image/jpeg")


def process_uploaded_image(
    raw_bytes: bytes,
    source_label: str,
    max_long_edge: int = _MAX_IMAGE_LONG_EDGE,
) -> tuple[bytes, str] | None:
    """camera_input / file_uploader から得た画像を OCR 用に前処理する共通関数。

    実施する処理（要件に対応）:
      1. 取得直後にデバッグ情報（ファイル形式・元サイズ・解像度）を表示する
      2. PIL.Image.open() の失敗など例外は握り潰さず st.error() で表示する
      3. EXIF の Orientation に基づく向き補正（iPhone 写真は横向きになりがち）
      4. 長辺が max_long_edge を超える場合は自動リサイズする
      5. 補正後の画像をプレビュー表示する

    Args:
        raw_bytes:    画像のバイナリ（camera_input/file_uploader の getvalue()）。
        source_label: プレビューやログ用のラベル（例: "カメラ撮影" / "アップロード"）。
        max_long_edge: リサイズ後の長辺の最大ピクセル数。

    Returns:
        (processed_bytes, mime_type) 成功時。
        None 失敗時（原因は st.error() で画面に表示済み）。
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    # ── 取得直後のデバッグ情報（バイト数） ──────────────────────────────
    st.caption(f"🔎 デバッグ情報（{source_label}）")
    debug: dict[str, str] = {"データサイズ": f"{len(raw_bytes):,} bytes"}

    if not raw_bytes:
        st.error("画像データが空です。撮影／アップロードをやり直してください。")
        return None

    # ── PIL で読み込み（例外は握り潰さない） ────────────────────────────
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()  # ここで実際にデコードし、壊れた画像なら例外を発生させる
    except UnidentifiedImageError as e:
        st.error(
            "画像形式を認識できませんでした。"
            "HEIC など未対応の形式の可能性があります。JPEG/PNG でお試しください。"
        )
        st.exception(e)
        return None
    except Exception as e:  # noqa: BLE001 - 例外を必ず可視化する（要件2）
        st.error("画像の読み込みに失敗しました（PIL.Image.open）。")
        st.exception(e)
        return None

    # デバッグ情報（形式・解像度）を追記して表示（要件1）
    debug["画像形式"] = img.format or "不明"
    debug["解像度（元）"] = f"{img.width} × {img.height} px"
    debug["カラーモード"] = img.mode

    # ── EXIF Orientation による向き補正（要件6） ────────────────────────
    try:
        img = ImageOps.exif_transpose(img)
        debug["EXIF向き補正"] = "適用（またはEXIFなし）"
    except Exception as e:  # noqa: BLE001 - 補正失敗も握り潰さず表示するが処理は継続
        st.warning("EXIF の向き補正に失敗しました。元の向きのまま処理を続けます。")
        st.exception(e)

    # ── 大きすぎる画像の自動リサイズ（要件4） ───────────────────────────
    try:
        long_edge = max(img.width, img.height)
        if long_edge > max_long_edge:
            scale = max_long_edge / float(long_edge)
            new_size = (round(img.width * scale), round(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)
            debug["リサイズ"] = f"{new_size[0]} × {new_size[1]} px（長辺{max_long_edge}pxに縮小）"
        else:
            debug["リサイズ"] = "不要（上限以下）"
    except Exception as e:  # noqa: BLE001
        st.error("画像のリサイズに失敗しました。")
        st.exception(e)
        return None

    # ── OCR 送信用に JPEG へ再エンコード（形式を統一） ──────────────────
    # iPhone 由来の HEIC/大サイズを、Gemini が確実に扱える JPEG に正規化する。
    try:
        rgb = img.convert("RGB")  # JPEG は透過を持てないため RGB 化
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=90)
        processed_bytes = buf.getvalue()
        mime_type = "image/jpeg"
        debug["変換後"] = f"JPEG / {len(processed_bytes):,} bytes"
    except Exception as e:  # noqa: BLE001
        st.error("画像の変換（JPEGエンコード）に失敗しました。")
        st.exception(e)
        return None

    # デバッグ情報テーブルを表示（要件1）
    st.table({"項目": list(debug.keys()), "値": list(debug.values())})

    # ── プレビュー表示（要件3・補正後の画像を必ず表示） ─────────────────
    st.image(processed_bytes, caption=f"{source_label}（補正・リサイズ後）",
             use_column_width=True)

    return processed_bytes, mime_type


def _run_ocr_and_fill_form(image_bytes: bytes, mime_type: str) -> None:
    """前処理済み画像で OCR を実行し、結果を本文フォームへ流し込む。

    OCR の例外は握り潰さず st.error()/AIエラー表示で必ず可視化する（要件2）。
    """
    try:
        with st.spinner("🖼️ Gemini が画像を読み取り中です..."):
            text = extract_text_from_image(image_bytes, mime_type=mime_type)
        if text:
            st.session_state["memo_raw_text"] = text
            st.session_state["memo_source_type"] = "ocr"
            st.session_state["ocr_just_done"] = True
            st.rerun()
        else:
            st.warning(
                "文字を読み取れませんでした。より鮮明な画像で再度お試しください。"
            )
    except AIAnalysisError as e:
        _show_ai_error(e)
    except Exception as e:  # noqa: BLE001 - OCR 失敗を必ず表示する（要件2）
        st.error("OCR の実行に失敗しました。")
        st.exception(e)


# ─── サイドバー（メモ登録フォーム） ──────────────────────────────────────────

def _render_input_tabs() -> None:
    """メモ本文の入力方法をタブで提供する（テキスト入力 / 画像から読み取り）。

    ここでは「メモ本文（raw_text）を作る」ことだけを行う入力補助。
    OCR 結果は session_state["memo_raw_text"] に入れ、下のフォームの
    text_area（key="memo_raw_text"）に反映される。確認・編集は必ずフォーム側で行う。
    要約・分類・タグ付け・保存は一切ここでは行わない（既存フローに委譲）。

    画像入力は「カメラ撮影」と「ファイルアップロード」の両方に対応し、
    どちらも共通の process_uploaded_image() で前処理（デバッグ表示・EXIF補正・
    自動リサイズ・プレビュー）してから OCR にかける。
    """
    tab_text, tab_image = st.tabs(["📝 テキスト入力", "📷 画像から読み取り"])

    with tab_text:
        st.caption("下のフォームにテキストを貼り付けて登録します（従来どおり）。")

    with tab_image:
        st.caption(
            "紙のメモや資料を撮影・アップロードすると、Gemini が文字を読み取ります。"
            "読み取り後、下のフォームで内容を確認・修正してから登録してください。"
        )

        input_mode = st.radio(
            "画像の入力方法",
            options=["カメラで撮影", "ファイルをアップロード"],
            horizontal=True,
            key="ocr_input_mode",
        )

        # まず OCR を実行せず「正常に表示できる状態」を作り、その後 OCR ボタンを出す。
        processed: tuple[bytes, str] | None = None
        source_label = ""

        if input_mode == "カメラで撮影":
            # iPhone Safari では JPEG が返る。撮影後は下でプレビュー＆デバッグ表示する。
            shot = st.camera_input("カメラで撮影", key="ocr_camera")
            if shot is not None:
                source_label = "カメラ撮影"
                processed = process_uploaded_image(shot.getvalue(), source_label)
        else:
            uploaded = st.file_uploader(
                "画像をアップロード",
                type=["png", "jpg", "jpeg", "webp", "heic", "heif"],
                key="ocr_uploader",
                help="説明会資料・手書きメモなどの画像（PNG / JPEG / WebP など）",
            )
            if uploaded is not None:
                source_label = "アップロード画像"
                processed = process_uploaded_image(uploaded.getvalue(), source_label)

        # プレビューが正常に表示できたときだけ OCR ボタンを出す（要件: 表示→OCR）
        if processed is not None:
            proc_bytes, proc_mime = processed
            if st.button("🔍 この画像から文字を読み取る（OCR）",
                         use_container_width=True, key="ocr_run"):
                _run_ocr_and_fill_form(proc_bytes, proc_mime)

    # OCR 完了直後の案内（フォーム側での確認・修正を促す）
    if st.session_state.pop("ocr_just_done", False):
        st.success(
            "✅ 読み取り結果を下のフォームに反映しました。"
            "内容を確認・修正してから「AIで分析して保存」を押してください。"
        )


def sidebar_form(sheets: SheetsService | None, sheets_error: Exception | None) -> None:
    with st.sidebar:
        st.title("📝 メモを登録")

        # Gemini 未設定の場合は登録フォームを無効化して案内
        if not settings.is_ai_ready:
            st.warning("⚠️ `GEMINI_API_KEY` が未設定のため登録できません。")
            st.caption("セットアップ画面の手順に従って `.env` を設定してください。")
            return

        # Sheets 未設定の場合は保存不可であることを明示
        if not settings.is_sheets_ready:
            st.warning("⚠️ `GOOGLE_SPREADSHEET_ID` が未設定のため保存できません。")
            st.caption("セットアップ画面の手順に従って `.env` を設定してください。")
            return

        # Sheets 接続失敗の場合は詳細エラーを表示
        if sheets is None:
            st.error("❌ Google Sheets に接続できません")
            if sheets_error is not None:
                with st.expander("🔍 エラー詳細（クリックで展開）", expanded=True):
                    st.exception(sheets_error)
            st.caption("PowerShell のログも確認してください。")
            return

        st.caption("テキストをコピペ / 画像から読み取り → AIが自動分類・要約します")

        # ── 入力方法タブ（テキスト入力 / 画像から読み取り[OCR]） ──────────
        # OCR は入力補助。結果は下のフォームの本文欄に反映され、確認・修正できる。
        _render_input_tabs()

        # ── スプレッドシートを開くリンク ──────────────────────────────────
        if settings.spreadsheet_url:
            st.link_button(
                "📊 スプレッドシートを開く",
                settings.spreadsheet_url,
                use_container_width=True,
                help="Googleスプレッドシートを新しいタブで開きます。",
            )

        # ── 接続テストボタン ─────────────────────────────────────────────
        with st.expander("🔧 接続テスト / メンテナンス"):
            if st.button("スプレッドシート接続を確認", use_container_width=True):
                try:
                    result = sheets.ping()
                    st.success("✅ 接続成功")
                    st.write(f"**スプレッドシート名**: {result['spreadsheet_title']}")
                    st.write(f"**対象シート**: {result['target_sheet']}")
                    st.write(f"**シート一覧**: {', '.join(result['sheet_names'])}")
                    st.write(f"**列数**: {len(result['columns'])}")
                    if not result["has_memos_sheet"]:
                        st.warning(
                            f"⚠️ シート `{result['target_sheet']}` が見つかりません。"
                        )
                except Exception as e:
                    st.error("接続テスト失敗")
                    st.exception(e)

            st.divider()
            st.caption(
                "既存シートを企業研究専用の列構成に整理します。"
                "面接・ES・インターン専用列は取り除かれ、企業研究データは"
                "列名ベースで安全に保持されます。"
            )
            if st.button("🏢 企業研究シートに移行（列を整理）", use_container_width=True):
                try:
                    res = sheets.migrate_layout()
                    st.success(
                        f"✅ 移行しました（{res['migrated_rows']} 行）。\n\n"
                        f"列を企業研究専用（{len(res['new_columns'])}列）に整理しました。"
                    )
                    with st.expander("移行の詳細"):
                        st.write("**移行前の列:**")
                        st.code("\n".join(res["old_columns"]) or "（なし）")
                        st.write("**移行後の列:**")
                        st.code("\n".join(res["new_columns"]))
                    # キャッシュを消して一覧を再取得させる
                    _clear_memo_caches()
                except Exception as e:
                    st.error("移行に失敗しました。")
                    st.exception(e)

        with st.form("memo_form", clear_on_submit=True):
            raw_text = st.text_area(
                "メモ本文 *",
                key="memo_raw_text",   # OCR結果はこのkey経由で反映される（確認・修正可）
                placeholder=(
                    "説明会のメモ、面接の感想、企業研究のメモなど、\n"
                    "テキストをそのままコピペしてください。\n"
                    "「画像から読み取り」タブで撮影画像のOCR結果もここに入ります。\n"
                    "AIが自動で企業名・カテゴリ・要約を生成します。"
                ),
                height=220,
                help="1〜10,000文字。企業名・カテゴリは省略可。OCR結果は編集できます。",
            )
            company_hint = st.text_input(
                "企業名（任意）",
                placeholder="例: 株式会社○○（省略するとAIが推定）",
            )
            type_hint = st.selectbox(
                "メモ種別（任意）",
                options=["AI自動分類"] + MEMO_TYPES,
                help="省略するとAIが本文から5分類のいずれかに自動判定します",
            )
            submitted = st.form_submit_button(
                "🤖 AIで分析して保存",
                use_container_width=True,
                type="primary",
            )

        if submitted:
            if not raw_text.strip():
                st.error("メモ本文を入力してください。")
                return
            if sheets is None:
                st.error("Sheets に接続できていません。サイドバーのエラー詳細を確認してください。")
                return
            _submit_memo(
                sheets=sheets,
                raw_text=raw_text.strip(),
                company_hint=company_hint.strip() or None,
                type_hint=None if type_hint == "AI自動分類" else type_hint,
                # OCR 経由で本文を取り込んだ場合は "ocr"、通常は "paste"
                source_type=st.session_state.pop("memo_source_type", "paste"),
            )

        if "last_saved" in st.session_state:
            saved = st.session_state.last_saved
            if st.session_state.get("last_saved_partial"):
                st.warning(
                    f"⚠️ 保存しました（要確認）\n\n"
                    f"**{saved.company_name}** / {saved.memo_type}\n\n"
                    f"AI分析が不完全でした。一覧から内容を確認・編集してください。"
                )
            else:
                st.success(
                    f"✅ 保存しました\n\n**{saved.company_name}** / {saved.memo_type}"
                )

        st.divider()
        st.caption("📋 一覧は右側のメインエリアに表示されます")
        st.caption("🔄 登録後は自動で一覧が更新されます")


def _show_ai_error(err: AIAnalysisError) -> None:
    """
    分類済み AIAnalysisError の原因を日本語で表示する。

    - title:      原因の見出し（例:「🚦 1日あたりの無料利用上限に達しました」）
    - guidance:   対処方法
    - retry_after: 再試行の目安（レート制限時）
    - raw_detail: 技術詳細（折りたたみ）
    """
    title = getattr(err, "title", None) or "AI分析に失敗しました"
    guidance = getattr(err, "guidance", "") or ""
    retry_after = getattr(err, "retry_after", None)
    raw_detail = getattr(err, "raw_detail", "") or str(err)

    st.error(f"{title}\n\nメモは保存されていません。")
    if guidance:
        st.markdown(guidance)
    if retry_after:
        if retry_after < 90:
            st.info(f"⏱️ 目安として約 {retry_after} 秒後に再試行できます。")
        else:
            st.info(f"⏱️ 目安として約 {round(retry_after / 60)} 分後に再試行できます。")
    with st.expander("🔍 エラー詳細（技術情報）", expanded=False):
        st.code(raw_detail)


def _submit_memo(
    sheets: SheetsService,
    raw_text: str,
    company_hint: str | None,
    type_hint: str | None,
    source_type: str = "paste",
) -> None:
    # 1. save_memo 開始
    logger.info("[save_memo] 開始 (本文長=%d)", len(raw_text))

    # ── Gemini 分析 ───────────────────────────────────────────────────────
    with st.spinner("✨ Gemini が分析中です... (5〜15秒)"):
        try:
            logger.info("[save_memo] 2. Gemini分析 開始")
            analysis = analyze_memo(
                raw_text=raw_text,
                company_hint=company_hint,
                category_hint=type_hint,
            )
            logger.info("[save_memo] 3. Gemini分析 成功")
            logger.info(
                "[save_memo] 4. Gemini分析 結果: type=%s company=%s sections=%d件 partial=%s",
                analysis.memo_type, analysis.company_name,
                len(analysis.sections), getattr(analysis, "is_partial", False),
            )
        except AIAnalysisError as e:
            logger.error("[save_memo] Gemini分析 失敗: kind=%s %s", getattr(e, "kind", "?"), e)
            # 構造化済み（title を持つ）ならそのまま、そうでなければメッセージを
            # その場で分類して日本語の原因を表示する（古いモジュールが残っていても
            # 原因が分かるようにするための保険）。
            if getattr(e, "title", None):
                _show_ai_error(e)
            else:
                _show_ai_error(_classify_api_error(e))
            return
        except Exception as e:
            logger.exception("[save_memo] Gemini分析 予期しないエラー")
            # 予期しない例外も可能な限り原因分類して日本語表示する
            _show_ai_error(_classify_api_error(e))
            return

    # メモ種別: ユーザーが種別を明示していればそれを優先、なければ AI 判定
    final_memo_type = type_hint or analysis.memo_type

    # ── MemoEntry 生成 ────────────────────────────────────────────────────
    memo = MemoEntry(
        raw_text=raw_text,
        company_name=company_hint or analysis.company_name,
        category=final_memo_type,   # 分類=種別で統一（5分類）
        tags=analysis.keywords,
        source_type=source_type,
        memo_type=final_memo_type,
        detailed_summary=analysis.detailed_summary,
        sections=analysis.sections,
        keywords=analysis.keywords,
        next_action=analysis.next_action,
    )

    if getattr(analysis, "is_partial", False):
        st.warning(
            "⚠️ AI分析結果が不完全な可能性があります（レスポンスが途中で切れました）。\n\n"
            "取得できた範囲で保存します。**内容を確認し、必要に応じて編集してください。**\n"
            "メモが長すぎる場合は、分割して登録すると精度が上がります。"
        )

    # ── 保存先シートの選択（メモ種別と保存先シートを完全連動） ──────────
    # 5分類はすべて SHEET_CONFIG にシートを持つ。該当しない場合のみ企業研究へ。
    target_type = memo.memo_type if memo.memo_type in SHEET_CONFIG else "企業研究"
    target_sheet_name = SHEET_CONFIG[target_type].sheet_name

    # 保存前ログ（指定フォーマット）
    logger.info("[AI分類]")
    logger.info("種別: %s", target_type)
    logger.info("保存先: %s", target_sheet_name)

    # 対象種別のシートサービスを取得（企業研究以外は別シート）
    target_sheets, target_err = get_sheets(target_type)
    if target_sheets is None:
        logger.error("[save_memo] 保存先シート(%s)接続失敗", target_type)
        st.error(f"❌ 「{target_type}」シート（{target_sheet_name}）への接続に失敗しました。")
        if target_err:
            st.exception(target_err)
        return
    sheets = target_sheets

    # 分類結果をサイト上に明示表示
    type_bg, type_fg = memo_type_color(target_type)
    st.markdown(
        f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-left:4px solid #2563eb;
        border-radius:8px;padding:12px 16px;margin:8px 0;">
        <div style="font-size:0.8rem;color:#64748b;font-weight:700;">🤖 AI分類結果</div>
        <div style="margin-top:6px;">
          種別: <span style="background:{type_bg};color:{type_fg};padding:2px 10px;
          border-radius:999px;font-weight:700;">{target_type}</span>
          &nbsp;→&nbsp; 保存先シート: <strong>{target_sheet_name}</strong>
        </div></div>""",
        unsafe_allow_html=True,
    )

    # ── Google Sheets 保存（append 検証付き） ────────────────────────────
    try:
        logger.info("[save_memo] 5. Google Sheets append 実行 id=%s", memo.id)
        with st.spinner("💾 Google スプレッドシートに保存中..."):
            result = sheets.append_memo(memo)   # 失敗時は例外 / 行が増えなければ例外
        logger.info("[save_memo] 6. append 実行結果: %s", result)
        logger.info(
            "[save_memo] 7. append後の行数: %d（前=%d）",
            result["rows_after"], result["rows_before"],
        )
    except ValueError as e:
        # 列数不一致・行が増えていない等の検証エラー
        logger.error("[save_memo] Sheets append 検証エラー: %s", e)
        st.error("❌ 保存に失敗しました（スプレッドシートに行が追加されませんでした）。")
        st.markdown(
            "**考えられる原因:**\n"
            "- 保存列数とヘッダー列数の不一致\n"
            "- `memos` シートが存在しない／範囲指定の不整合"
        )
        st.exception(e)
        return
    except Exception as e:
        logger.exception("[save_memo] Sheets append 失敗")
        st.error("❌ スプレッドシートへの保存に失敗しました。")
        st.markdown(
            "**考えられる原因:**\n"
            "- サービスアカウントに編集者権限がない\n"
            "- `GOOGLE_SPREADSHEET_ID` の誤り\n"
            "- ネットワーク／API エラー"
        )
        st.exception(e)
        return

    # ── append 成功を確認してから成功状態をセット ────────────────────────
    logger.info("[save_memo] 8. save_memo 完了 id=%s", memo.id)
    st.session_state.last_saved = memo
    st.session_state.last_saved_partial = getattr(analysis, "is_partial", False)
    st.session_state.last_saved_range = result["updated_range"]

    # 一覧は全シートを集約表示するため、保存先シートに関わらず新規メモは自動で
    # 一覧に現れる。特定種別に絞り込んで他シートを隠すことはしない（全件表示）。
    st.session_state["filter_memo_type"] = "すべての種別"

    _clear_memo_caches()
    st.rerun()


# ─── メインエリア（一覧表示） ─────────────────────────────────────────────────

def main_area() -> None:
    """全シート（企業研究/ES・履歴書/面接/インターン/選考結果）を集約して一覧表示する。"""
    title_col, link_col = st.columns([3, 1])
    with title_col:
        st.title("📋 保存済みメモ一覧")
    with link_col:
        if settings.spreadsheet_url:
            st.link_button(
                "📊 スプレッドシートを開く",
                settings.spreadsheet_url,
                use_container_width=True,
                help="Googleスプレッドシートを新しいタブで開きます。",
            )

    # ── 自動更新状態をクエリパラメータから復元（JS リロードをまたいで継続） ──
    # トグル widget を生成する前に session_state を設定しておくと初期値になる。
    # 一度でも widget が値を持ったら以降は widget 側が管理するため上書きしない。
    if "auto_refresh_enabled" not in st.session_state:
        try:
            ar = st.query_params.get("autorefresh")
        except Exception:
            ar = None
        if ar is not None:
            st.session_state["auto_refresh_enabled"] = True
            try:
                st.session_state["auto_refresh_interval"] = int(ar)
            except (TypeError, ValueError):
                st.session_state["auto_refresh_interval"] = 30

    # ── 全シートからメモを集約取得（session_state でキャッシュ） ─────────────
    cache_key = "memos_cache::__all__"
    # 「🔄 更新」等で強制再取得フラグが立っていれば、シート側 TTL も無視して読む。
    # 自動更新 ON のときは、JS リロード後の初回取得でも必ず最新を読む
    # （リロードで session_state のフラグは消えるため、トグル状態で判定する）。
    force = st.session_state.pop("force_refresh_memos", False)
    if st.session_state.get("auto_refresh_enabled"):
        force = True
        _clear_memo_caches()
    if force or cache_key not in st.session_state:
        with st.spinner("全シートからメモを取得中..."):
            memos, counts, errors = get_all_memos_all_sheets(force_refresh=force)
            st.session_state[cache_key] = memos
            st.session_state["memos_counts"] = counts
            st.session_state["memos_errors"] = errors

    memos: list[MemoEntry] = st.session_state[cache_key]
    counts: dict[str, int] = st.session_state.get("memos_counts", {})
    errors: list[tuple[str, Exception]] = st.session_state.get("memos_errors", [])

    # 全シート接続不可（1件も取得できず、かつ全種別でエラー）の場合
    if not memos and errors and len(errors) >= len(SHEET_CONFIG):
        st.error("Google Sheets に接続できませんでした。")
        with st.expander("🔍 エラー詳細（クリックで展開）", expanded=True):
            for mtype, err in errors:
                st.markdown(f"**{mtype}**")
                st.exception(err)
        st.markdown(
            "**よくある原因:**\n"
            "- `service_account.json` が見つからない（パスを確認）\n"
            "- スプレッドシートにサービスアカウントの **編集者** 権限がない\n"
            "- `GOOGLE_SPREADSHEET_ID` の値が間違っている\n\n"
            "PowerShell のログにも詳細が出力されています。"
        )
        return

    # 一部シートのみ失敗した場合は警告だけ出して続行
    if errors:
        with st.expander(f"⚠️ 一部シートの取得に失敗しました（{len(errors)}件）", expanded=False):
            for mtype, err in errors:
                st.markdown(f"**{mtype}**")
                st.exception(err)

    # ── フィルター（企業名 / メモ種別 / 更新 / 自動更新） ────────────────────
    col_f1, col_f2, col_reload, col_auto = st.columns([3, 2, 1, 2])
    with col_f1:
        filter_company = st.text_input(
            "企業名フィルタ",
            placeholder="🔍 企業名で絞り込み（部分一致）",
            label_visibility="collapsed",
        )
    with col_f2:
        filter_memo_type = st.selectbox(
            "メモ種別",
            options=["すべての種別"] + MEMO_TYPES,
            key="filter_memo_type",
            label_visibility="collapsed",
        )
    with col_reload:
        if st.button("🔄 更新", use_container_width=True):
            _clear_memo_caches()
            st.session_state["force_refresh_memos"] = True
            st.rerun()
    with col_auto:
        auto_refresh = st.toggle(
            "自動更新",
            key="auto_refresh_enabled",
            help="有効にすると、一定間隔でスプレッドシートの最新内容を自動取得します。",
        )
        interval = st.selectbox(
            "更新間隔",
            options=[10, 30, 60, 120],
            index=1,
            format_func=lambda s: f"{s}秒ごと",
            key="auto_refresh_interval",
            label_visibility="collapsed",
            disabled=not auto_refresh,
        )
        # 自動更新は JS の location.reload() で再取得する。リロードで session_state
        # は失われるため、ON/OFF と間隔をクエリパラメータに保存して状態を維持する。
        try:
            if auto_refresh:
                st.query_params["autorefresh"] = str(int(interval))
            else:
                if "autorefresh" in st.query_params:
                    del st.query_params["autorefresh"]
        except Exception:
            pass

    # ── シート別の件数サマリー ───────────────────────────────────────────────
    if counts:
        summary = "　".join(
            f"{mtype}: **{counts.get(mtype, 0)}**" for mtype in SHEET_CONFIG.keys()
        )
        st.caption(f"シート別件数　{summary}")

    # ── フィルタリング ───────────────────────────────────────────────────────
    filtered = memos
    if filter_company:
        filtered = [m for m in filtered if filter_company.lower() in m.company_name.lower()]
    if filter_memo_type != "すべての種別":
        filtered = [m for m in filtered if m.memo_type == filter_memo_type]

    total = len(memos)
    shown = len(filtered)

    if total == 0:
        st.info("まだメモが登録されていません。左のフォームから登録してください。")
        return

    st.caption(
        f"全 {total} 件" + (f"　→　絞り込み結果: **{shown} 件**" if shown != total else "")
    )
    if shown == 0:
        st.warning("条件に一致するメモがありません。")
        return

    st.markdown("<hr class='section'>", unsafe_allow_html=True)

    for memo in filtered:
        # 編集・削除はメモが属するシートのサービスへ正しくルーティングする
        memo_sheets, memo_err = get_sheets_for_memo(memo)
        if memo_sheets is None:
            # そのシートのサービスが取れない場合でも、カードだけは表示する
            render_memo_card(memo)
            st.caption(f"⚠️ 「{memo.memo_type}」シートに接続できず、編集・削除は利用できません。")
            continue
        _render_memo_row(memo, memo_sheets)

    # ── 自動更新（任意・標準機能のみで実装） ─────────────────────────────────
    # 有効時は指定間隔だけ待ってから、シート側キャッシュを含めて強制再取得する。
    # 編集フォーム・削除確認の表示中は、操作を妨げないよう自動更新を止める。
    _maybe_auto_refresh()


def _maybe_auto_refresh() -> None:
    """自動更新トグルが ON なら、間隔ごとにスプレッドシート最新を取得して再描画する。"""
    if not st.session_state.get("auto_refresh_enabled"):
        return
    # 編集・削除の操作中は自動更新しない（入力内容を失わないため）
    if st.session_state.get("editing_memo_id") or st.session_state.get("confirm_delete_id"):
        st.caption("⏸️ 編集・削除の操作中は自動更新を一時停止しています。")
        return

    interval = int(st.session_state.get("auto_refresh_interval", 30))
    st.caption(f"🔄 自動更新: {interval}秒ごとにスプレッドシートの最新を取得します。")

    # ── 非ブロッキング自動更新 ────────────────────────────────────────────
    # 以前は time.sleep(interval) でスクリプトをブロックしていたが、
    # その間 WebSocket がハートビートに応答できず、スマホ（省電力・断続回線）
    # では接続が切れて "CONNECTING" のまま復帰できなくなる原因になっていた。
    #
    # 代わりに、ブラウザ側で setTimeout → location.reload() する。
    # スクリプトは即座に終了するので WebSocket は維持され、指定間隔後に
    # ブラウザがページを再読み込みして最新を取得する（TTL/強制再取得で反映）。
    st.session_state["force_refresh_memos"] = True
    st.markdown(
        f"""
        <script>
        // 二重登録を防ぐため、既存タイマーがあればクリアしてから設定する
        if (window.__memoAutoRefreshTimer) {{ clearTimeout(window.__memoAutoRefreshTimer); }}
        window.__memoAutoRefreshTimer = setTimeout(function() {{
            window.location.reload();
        }}, {interval * 1000});
        </script>
        """,
        unsafe_allow_html=True,
    )


def _intern_name_of(memo: MemoEntry) -> str:
    """メモから「インターン名」に相当する情報を推定する。

    インターン種別なら sections の「インターン概要」等を使う。
    見つからなければ空文字（＝企業名だけでお気に入り登録）。
    """
    if memo.memo_type != "インターン":
        return ""
    for key in ("インターン概要", "プログラム内容", "開催形式"):
        val = memo.sections.get(key, "").strip()
        if val:
            return val[:40]
    return ""


def _render_favorite_button(memo: MemoEntry) -> None:
    """カード下にお気に入りトグルボタンを描画する。

    - 未登録: 「☆お気に入り追加」
    - 登録済: 「★お気に入り済み」（押すと解除）
    保存項目は 企業名 / インターン名 / 登録日時（FavoritesManager が付与）。
    """
    fav = get_favorites_manager()
    intern_name = _intern_name_of(memo)
    is_fav = fav.is_favorite(memo.company_name, intern_name)

    label = "★お気に入り済み" if is_fav else "☆お気に入り追加"
    btn_type = "primary" if is_fav else "secondary"

    col, _spacer = st.columns([2, 4])
    with col:
        if st.button(label, key=f"fav_{memo.id}", use_container_width=True, type=btn_type):
            # トグル: 登録済みなら解除、未登録なら追加（重複は Manager が防止）
            now_added = fav.toggle(memo.company_name, intern_name)
            if now_added:
                st.toast(f"⭐ お気に入りに追加: {memo.company_name}")
            else:
                st.toast(f"☆ お気に入りを解除: {memo.company_name}")
            st.rerun()


def _render_memo_row(memo: MemoEntry, sheets: SheetsService) -> None:
    """1件分のメモカード＋編集／削除を描画する"""
    editing_id  = st.session_state.get("editing_memo_id")
    confirm_id  = st.session_state.get("confirm_delete_id")

    # ── 編集フォーム表示中 ────────────────────────────────────────────────
    if editing_id == memo.id:
        _render_edit_form(memo, sheets)
        return

    # ── 削除確認表示中 ────────────────────────────────────────────────────
    if confirm_id == memo.id:
        _render_delete_confirm(memo, sheets)
        return

    # ── 通常カード ────────────────────────────────────────────────────────
    render_memo_card(memo)

    # ── お気に入りトグル ──────────────────────────────────────────────────
    _render_favorite_button(memo)

    with st.expander("詳細を見る"):
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            st.markdown("**企業名**")
            st.write(memo.company_name)
            st.markdown("**保存先シート**")
            _sheet = SHEET_CONFIG[memo.memo_type].sheet_name if memo.memo_type in SHEET_CONFIG else "memos"
            st.write(_sheet)
        with dcol2:
            st.markdown("**登録日時**")
            st.write(format_date(memo.created_at))
            st.markdown("**メモ種別**")
            type_bg, type_fg = memo_type_color(memo.memo_type)
            st.markdown(
                f'<span class="badge" style="background:{type_bg};color:{type_fg}">'
                f'{memo.memo_type}</span>',
                unsafe_allow_html=True,
            )

        # ── キーワード ──────────────────────────────────────────────────
        if memo.keywords:
            st.markdown("**🔑 キーワード**")
            st.write("　".join(f"`{k}`" for k in memo.keywords))

        # ── 次回アクション ──────────────────────────────────────────────
        if memo.next_action:
            st.markdown("**📌 次回アクション**")
            st.info(memo.next_action)

        # ── 詳細な構造化要約（見出し付き Markdown） ────────────────────
        st.markdown("**📝 AI 構造化要約**")
        content = memo.detailed_summary
        if not content and memo.sections:
            # 保険: detailed が無ければ sections から見出しを組む
            content = "\n\n".join(
                f"## {name}\n・{val}" for name, val in memo.sections.items()
            )
        st.markdown(content or "（なし）")

        st.divider()
        st.markdown("**📄 元のメモ**")
        st.text_area(
            label="元のメモ",
            value=memo.raw_text,
            height=160,
            disabled=True,
            label_visibility="collapsed",
            key=f"raw_{memo.id}",
        )

        st.divider()
        btn_col1, btn_col2, _ = st.columns([1, 1, 4])
        with btn_col1:
            if st.button("✏️ 編集", key=f"edit_{memo.id}", use_container_width=True):
                st.session_state.editing_memo_id = memo.id
                st.rerun()
        with btn_col2:
            if st.button("🗑️ 削除", key=f"del_{memo.id}", use_container_width=True):
                st.session_state.confirm_delete_id = memo.id
                st.rerun()


def _render_edit_form(memo: MemoEntry, sheets: SheetsService) -> None:
    """編集フォームをインラインで描画する"""
    type_bg, type_fg = memo_type_color(memo.memo_type)
    st.markdown(
        f"""<div class="memo-card" style="border-left: 4px solid #2563eb;">
        <div class="memo-card-header">
          <span class="company-name">✏️ 編集中: {memo.company_name}</span>
          <span class="badge" style="background:{type_bg};color:{type_fg}">{memo.memo_type}</span>
          <span class="date-text">{format_date(memo.created_at)}</span>
        </div></div>""",
        unsafe_allow_html=True,
    )

    with st.form(key=f"edit_form_{memo.id}"):
        col_left, col_right = st.columns(2)
        with col_left:
            new_company = st.text_input("企業名", value=memo.company_name)
            new_memo_type = st.selectbox(
                "メモ種別",
                options=MEMO_TYPES,
                index=MEMO_TYPES.index(memo.memo_type) if memo.memo_type in MEMO_TYPES else 0,
                help="種別を変更すると次回保存時に対応シートへ移動します",
            )
        with col_right:
            new_keywords_str = st.text_input(
                "キーワード（カンマ区切り）",
                value=", ".join(memo.keywords),
                help="例: 技術職, リモート, 一次面接",
            )

        new_next_action = st.text_input(
            "次回アクション",
            value=memo.next_action,
            placeholder="例: 〇〇について追加調査する",
        )

        # ── セクション個別編集（現在のメモ種別の項目 ＋ 既存の項目） ──────
        st.markdown("**セクション（各50字以内推奨）**")
        section_names = list(SECTIONS_BY_TYPE.get(memo.memo_type, []))
        # 既にデータがあるが種別項目に無いセクションも編集できるよう追加
        for k in memo.sections.keys():
            if k not in section_names:
                section_names.append(k)

        new_sections_inputs: dict[str, str] = {}
        for name in section_names:
            new_sections_inputs[name] = st.text_input(
                name,
                value=memo.sections.get(name, ""),
                key=f"sec_{memo.id}_{name}",
            )

        new_raw = st.text_area("元のメモ本文", value=memo.raw_text, height=140)

        save_col, cancel_col, _ = st.columns([1, 1, 4])
        with save_col:
            save = st.form_submit_button("💾 保存", type="primary", use_container_width=True)
        with cancel_col:
            cancel = st.form_submit_button("キャンセル", use_container_width=True)

    if cancel:
        st.session_state.pop("editing_memo_id", None)
        st.rerun()

    if save:
        from datetime import datetime, timezone
        new_keywords = [k.strip() for k in new_keywords_str.split(",") if k.strip()]
        # 入力のあるセクションだけ辞書化
        new_sections = {
            name: val.strip()
            for name, val in new_sections_inputs.items()
            if val.strip()
        }
        # detailed_summary は編集後の sections から見出し付きで再生成
        new_detailed = "\n\n".join(
            f"## {name}\n・{val}" for name, val in new_sections.items()
        )

        updated = memo.model_copy(update={
            "company_name":     new_company.strip() or memo.company_name,
            "category":         new_memo_type,
            "memo_type":        new_memo_type,
            "keywords":         new_keywords,
            "tags":             new_keywords,
            "next_action":      new_next_action.strip(),
            "sections":         new_sections,
            "detailed_summary": new_detailed,
            "raw_text":         new_raw.strip(),
            "updated_at":       datetime.now(timezone.utc),
        })
        try:
            if new_memo_type != memo.memo_type:
                # 種別が変わった → 保存先シートが変わるため「旧シートから削除して
                # 新シートへ追加」で移動する（各シートは列構成が異なるため in-place
                # 更新では移動できない）。
                new_sheets, new_err = get_sheets(new_memo_type)
                if new_sheets is None:
                    st.error(f"「{new_memo_type}」シートに接続できず、種別を変更できません。")
                    if new_err:
                        st.exception(new_err)
                    return
                new_sheets.append_memo(updated)   # 新シートへ追加
                sheets.delete_memo(memo.id)       # 旧シートから削除
                new_sheets._invalidate_cache()
                sheets._invalidate_cache()
                st.session_state.pop("editing_memo_id", None)
                _clear_memo_caches()
                st.success(
                    f"✅ 種別を変更し「{new_memo_type}」シートへ移動しました"
                    f"（{updated.company_name}）"
                )
                st.rerun()
            else:
                # 種別据え置き → 同じシートで in-place 更新
                sheets.update_memo(updated)
                st.session_state.pop("editing_memo_id", None)
                _clear_memo_caches()
                sheets._invalidate_cache()
                st.success(f"✅ 更新しました（{updated.company_name} / {updated.memo_type}）")
                st.rerun()
        except Exception as e:
            st.error("更新に失敗しました。")
            st.exception(e)


def _render_delete_confirm(memo: MemoEntry, sheets: SheetsService) -> None:
    """削除確認バナーをインラインで描画する"""
    st.warning(
        f"🗑️ **本当に削除しますか？**\n\n"
        f"「**{memo.company_name}**」（{memo.memo_type}・{format_date(memo.created_at)}）を削除します。\n\n"
        "この操作はスプレッドシートの行を削除します。元に戻せません。"
    )
    confirm_col, cancel_col, _ = st.columns([1, 1, 4])
    with confirm_col:
        if st.button("🗑️ 削除する", key=f"exec_del_{memo.id}", type="primary", use_container_width=True):
            try:
                sheets.delete_memo(memo.id)
                st.session_state.pop("confirm_delete_id", None)
                _clear_memo_caches()
                sheets._invalidate_cache()
                st.success(f"削除しました（{memo.company_name}）")
                st.rerun()
            except Exception as e:
                st.error("削除に失敗しました。")
                st.exception(e)
    with cancel_col:
        if st.button("キャンセル", key=f"cancel_del_{memo.id}", use_container_width=True):
            st.session_state.pop("confirm_delete_id", None)
            st.rerun()


# ─── お気に入り一覧ページ ─────────────────────────────────────────────────────

def favorites_page() -> None:
    """お気に入りに登録した企業・インターンの一覧を表示する。"""
    st.title("⭐ お気に入り")
    fav = get_favorites_manager()
    items = fav.list_all()

    if not items:
        st.info("まだお気に入りがありません。ホームのカードから「☆お気に入り追加」で登録できます。")
        return

    st.caption(f"登録件数: **{len(items)}** 件")

    # 一括操作
    if st.button("🗑️ すべて解除", type="secondary"):
        fav.clear()
        st.toast("お気に入りをすべて解除しました")
        st.rerun()

    st.markdown("<hr class='section'>", unsafe_allow_html=True)

    for i, it in enumerate(items):
        # 企業名 / インターン名 / 登録日時 を表示
        intern_line = f"　—　{it.intern_name}" if it.intern_name else ""
        st.markdown(f"""
        <div class="memo-card">
          <div class="memo-card-header">
            <span class="company-name">⭐ {it.company_name}</span>
            <span class="date-text">{format_date(it.created_at)}</span>
          </div>
          <div class="summary-text">{it.intern_name or "（インターン名なし）"}</div>
        </div>
        """, unsafe_allow_html=True)
        col, _sp = st.columns([2, 4])
        with col:
            if st.button("★ 解除する", key=f"favrm_{i}", use_container_width=True):
                fav.remove(it.company_name, it.intern_name)
                st.toast(f"☆ 解除: {it.company_name}")
                st.rerun()


# ─── メモ登録ページ（モバイルでサイドバーが使いにくい場合の導線） ────────────

def memo_page() -> None:
    """メモ登録の導線ページ。登録フォーム自体はサイドバーに常設されている。"""
    st.title("📝 メモを登録")
    st.info(
        "メモの登録は左側のサイドバー（スマホでは左上の「≫」アイコンから開けます）"
        "の登録フォームから行えます。"
    )
    if settings.spreadsheet_url:
        st.link_button(
            "📊 スプレッドシートを開く",
            settings.spreadsheet_url,
            use_container_width=False,
        )


# ─── 分析ページ ───────────────────────────────────────────────────────────────

def analytics_page() -> None:
    """登録メモの簡易分析（種別別・企業別の件数集計）を表示する。"""
    st.title("📊 分析")

    force = st.session_state.pop("force_refresh_memos", False)
    memos, counts, errors = get_all_memos_all_sheets(force_refresh=force)

    if not memos:
        st.info("分析対象のメモがまだありません。")
        return

    total = len(memos)
    st.metric("登録メモ総数", f"{total} 件")

    # ── 種別別件数 ───────────────────────────────────────────────────────
    st.subheader("メモ種別別の件数")
    type_counts = {mtype: counts.get(mtype, 0) for mtype in SHEET_CONFIG.keys()}
    st.bar_chart(type_counts)

    # ── 企業別件数（上位10社） ───────────────────────────────────────────
    st.subheader("企業別の件数（上位10社）")
    company_counts: dict[str, int] = {}
    for m in memos:
        company_counts[m.company_name] = company_counts.get(m.company_name, 0) + 1
    top = dict(sorted(company_counts.items(), key=lambda x: x[1], reverse=True)[:10])
    if top:
        st.bar_chart(top)

    # ── お気に入り数 ─────────────────────────────────────────────────────
    fav = get_favorites_manager()
    st.metric("お気に入り登録数", f"{fav.count()} 件")


# ─── AIエージェント画面 ───────────────────────────────────────────────────────

_AGENT_EXAMPLES = [
    "次に何をすればいい？",
    "面接練習をしたい",
    "私の強みを分析して",
    "志望企業を比較して",
    "就活の傾向を分析して",
]


def get_conversation_manager() -> ConversationManager:
    """会話履歴マネージャ（data/conversations.json に永続化）。"""
    return ConversationManager()


def get_memory_manager() -> MemoryManager:
    """長期記憶マネージャ（data/long_term_memory.json に永続化）。"""
    return MemoryManager()


def agent_page() -> None:
    """就活AIエージェント画面。

    - 会話履歴（短期記憶）を永続化し、一覧・再開・削除・検索できる。
    - 長期記憶（就活プロフィール）を踏まえ、優先順位付き RAG で回答する。
    - 回答には「参考情報」を提示し、事実と推測を区別する。
    - ダッシュボードで就活状況を可視化する。
    """
    st.title("🤖 就活AIエージェント")

    if not settings.is_ai_ready:
        st.warning("⚠️ `GEMINI_API_KEY` が未設定のため、エージェントを利用できません。")
        return

    # 全シートからメモを集約（他画面と同じ取得経路を再利用）
    memos, counts, errors = get_all_memos_all_sheets()
    conv_mgr = get_conversation_manager()
    mem_mgr = get_memory_manager()

    tab_chat, tab_convs, tab_dash = st.tabs(["💬 相談する", "🗂️ 会話履歴", "📊 ダッシュボード"])

    with tab_chat:
        _agent_chat_tab(memos, conv_mgr, mem_mgr)
    with tab_convs:
        _agent_conversations_tab(conv_mgr)
    with tab_dash:
        _agent_dashboard_tab(memos, counts, conv_mgr, mem_mgr)


def _current_conv_id(conv_mgr: ConversationManager) -> str:
    """現在アクティブな会話IDを返す（無ければ新規作成）。"""
    cid = st.session_state.get("active_conv_id")
    if cid and conv_mgr.get(cid) is not None:
        return cid
    conv = conv_mgr.create(title="新しい会話", conv_type="一般")
    st.session_state["active_conv_id"] = conv.id
    return conv.id


def _agent_chat_tab(memos, conv_mgr: ConversationManager, mem_mgr: MemoryManager) -> None:
    """相談（チャット）タブ。会話は永続化され、面接練習は再開できる。"""
    total = len(memos)
    st.caption(f"参照可能なメモ: **{total}** 件")
    if total == 0:
        st.info("まだメモがありません。サイドバーから登録するとエージェントが活用できます。")

    # アクティブ会話
    cid = _current_conv_id(conv_mgr)
    conv = conv_mgr.get(cid)

    # 会話のヘッダー（タイトル・種別・進行状況）
    head_l, head_r = st.columns([3, 2])
    with head_l:
        st.markdown(f"**会話:** {conv.title}")
    with head_r:
        label = conv.progress_label()
        if label:
            st.markdown(f"**進行状況:** {label}")

    # 会話種別の選択（面接練習など）＋新規会話
    ctrl_l, ctrl_r = st.columns([3, 1])
    with ctrl_l:
        new_type = st.selectbox(
            "会話の種類", options=CONVERSATION_TYPES,
            index=CONVERSATION_TYPES.index(conv.conv_type) if conv.conv_type in CONVERSATION_TYPES else 4,
            key=f"conv_type_{cid}",
        )
        if new_type != conv.conv_type:
            conv.conv_type = new_type
            conv_mgr.save(conv)
    with ctrl_r:
        if st.button("🆕 新しい会話", use_container_width=True):
            newc = conv_mgr.create(title="新しい会話", conv_type="一般")
            st.session_state["active_conv_id"] = newc.id
            st.rerun()

    # 質問例
    with st.expander("💡 質問の例", expanded=(len(conv.messages) == 0)):
        ex_cols = st.columns(len(_AGENT_EXAMPLES))
        for col, ex in zip(ex_cols, _AGENT_EXAMPLES):
            with col:
                if st.button(ex, key=f"agent_ex_{ex}", use_container_width=True):
                    st.session_state["agent_pending_question"] = ex
                    st.rerun()

    # これまでの会話を表示（永続化された messages）
    for turn in conv.messages:
        role = turn.role if hasattr(turn, "role") else turn.get("role")
        content = turn.content if hasattr(turn, "content") else turn.get("content")
        refs = (turn.refs if hasattr(turn, "refs") else turn.get("refs")) or []
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(content)
            if refs:
                with st.expander(f"📎 参考情報（{len(refs)}件）"):
                    for r in refs:
                        st.markdown(_format_reference_line(r))

    # 入力
    pending = st.session_state.pop("agent_pending_question", None)
    typed = st.chat_input("質問を入力（例: 東邦液化ガスの面接練習をしたい）")
    question = pending or typed

    if question:
        # ユーザー発話を永続化
        conv_mgr.add_message(cid, "user", question)
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("🤖 長期記憶と関連メモを参照して回答中..."):
                try:
                    # 長期記憶・お気に入りを優先情報として渡す
                    lt = mem_mgr.load()
                    fav = get_favorites_manager()
                    fav_names = [f.company_name for f in fav.list_all()]
                    # 直近履歴（必要な場合のみ・全会話は送らない）
                    hist = [
                        {"role": (m.role if hasattr(m, "role") else m.get("role")),
                         "content": (m.content if hasattr(m, "content") else m.get("content"))}
                        for m in conv.messages
                    ]
                    resp: AgentResponse = ask_agent(
                        question=question,
                        memos=memos,
                        long_term_lines=lt.as_context_lines(),
                        favorites=fav_names,
                        history=hist,
                    )
                    st.markdown(resp.answer)

                    # 透明性: 参考情報を提示（長期記憶・メモ・お気に入りの由来）
                    if resp.info_sources:
                        with st.expander(f"🔎 参考情報（{len(resp.info_sources)}件）", expanded=True):
                            for s in resp.info_sources:
                                st.markdown(f"- {s}")

                    # アシスタント発話を永続化（参考情報も保存）
                    ref_dicts = [_ref_to_dict(r) for r in resp.references]
                    conv_mgr.add_message(cid, "assistant", resp.answer,
                                         refs=[{"line": s} for s in resp.info_sources] or ref_dicts)

                    # タイトル自動設定（最初の質問をタイトルにする）
                    conv = conv_mgr.get(cid)
                    if conv.title == "新しい会話":
                        conv_mgr.rename(cid, question[:30])

                    # 面接練習の進行状況を更新（アシスタント応答=1問進んだとみなす）
                    if conv.conv_type == "面接練習":
                        prev = conv.progress or {"current": 0, "total": 15}
                        cur = min(prev.get("current", 0) + 1, prev.get("total", 15))
                        conv_mgr.update_progress(cid, cur, prev.get("total", 15))

                    # 長期記憶を会話から更新（重要情報のみ抽出）
                    try:
                        convo_text = f"user: {question}\nassistant: {resp.answer}"
                        mem_mgr.extract_and_update(convo_text)
                    except Exception:  # noqa: BLE001 - 記憶更新失敗は会話を止めない
                        logger.warning("[Agent] 長期記憶の更新に失敗（会話は継続）")

                except AIAnalysisError as e:
                    _show_ai_error(e)
                except Exception as e:  # noqa: BLE001
                    st.error("エージェントの応答生成に失敗しました。")
                    st.exception(e)
        st.rerun()


def _agent_conversations_tab(conv_mgr: ConversationManager) -> None:
    """会話履歴タブ。一覧・検索・再開・削除。"""
    st.subheader("🗂️ 会話履歴")

    # 再開直後のフィードバック（st.tabs はプログラム切替不可のため案内で補う）
    resumed = st.session_state.pop("resumed_conv_title", None)
    if resumed:
        st.success(f"「{resumed}」を再開しました。上部の「💬 相談する」タブを開くと続きから会話できます。")

    query = st.text_input("🔍 会話を検索（タイトル・種別・本文）", key="conv_search")
    convs = conv_mgr.search(query) if query else conv_mgr.list_all()

    if not convs:
        st.info("保存された会話はまだありません。")
        return

    for c in convs:
        label = c.progress_label()
        prog = f"　進行状況 {label}" if label else ""
        with st.container():
            st.markdown(
                f'<div class="memo-card"><b>{c.title}</b>'
                f'<span class="badge" style="margin-left:8px">{c.conv_type}</span>'
                f'<div class="date-text">{format_date_iso(c.updated_at)}{prog}</div></div>',
                unsafe_allow_html=True,
            )
            col_open, col_del, _sp = st.columns([1, 1, 3])
            with col_open:
                if st.button("▶️ 再開", key=f"conv_open_{c.id}", use_container_width=True):
                    # アクティブ会話を切り替える。Streamlit 1.36 の st.tabs は
                    # プログラムからのタブ切替に非対応のため、「相談する」タブを
                    # 開くようユーザーへ明示的に案内する。
                    st.session_state["active_conv_id"] = c.id
                    st.session_state["resumed_conv_title"] = c.title
                    st.rerun()
            with col_del:
                if st.button("🗑️ 削除", key=f"conv_del_{c.id}", use_container_width=True):
                    conv_mgr.delete(c.id)
                    if st.session_state.get("active_conv_id") == c.id:
                        st.session_state.pop("active_conv_id", None)
                    st.rerun()


def _agent_dashboard_tab(memos, counts, conv_mgr: ConversationManager,
                         mem_mgr: MemoryManager) -> None:
    """ダッシュボードタブ。就活状況を可視化する。"""
    st.subheader("📊 ダッシュボード")

    # 企業数（ユニーク企業名）
    companies = {m.company_name for m in memos if m.company_name and m.company_name != "不明"}
    ocr_count = sum(1 for m in memos if getattr(m, "source_type", "") == "ocr")
    fav = get_favorites_manager()
    interview_practice = conv_mgr.count_by_type("面接練習")

    c1, c2, c3 = st.columns(3)
    c1.metric("登録企業数", f"{len(companies)}")
    c2.metric("メモ数", f"{len(memos)}")
    c3.metric("OCRメモ数", f"{ocr_count}")
    c4, c5, c6 = st.columns(3)
    c4.metric("お気に入り数", f"{fav.count()}")
    c5.metric("面接練習回数", f"{interview_practice}")
    c6.metric("会話数", f"{conv_mgr.count()}")

    # 志望業界分布（長期記憶より）
    lt = mem_mgr.load()
    st.markdown("**志望業界（長期記憶）**")
    if lt.志望業界:
        st.bar_chart({k: 1 for k in lt.志望業界})
    else:
        st.caption("まだ志望業界が抽出されていません（会話を重ねると蓄積されます）。")

    # 頻出キーワード（メモの keywords 集計 上位10）
    st.markdown("**頻出キーワード**")
    kw_counts: dict[str, int] = {}
    for m in memos:
        for k in (m.keywords or []):
            kw_counts[k] = kw_counts.get(k, 0) + 1
    top_kw = dict(sorted(kw_counts.items(), key=lambda x: x[1], reverse=True)[:10])
    if top_kw:
        st.bar_chart(top_kw)
    else:
        st.caption("キーワードがまだありません。")

    # 長期記憶の内容表示
    with st.expander("🧠 長期記憶（あなたの就活プロフィール）", expanded=False):
        lines = lt.as_context_lines()
        if lines:
            for ln in lines:
                st.markdown(f"- {ln}")
        else:
            st.caption("まだ長期記憶がありません。会話を重ねると自動で蓄積されます。")


def format_date_iso(iso_str: str) -> str:
    """ISO 文字列を YYYY/MM/DD 表示に整形する。"""
    try:
        from datetime import datetime as _dt
        return _dt.fromisoformat(iso_str).astimezone().strftime("%Y/%m/%d")
    except Exception:
        return str(iso_str)[:10]


def _ref_to_dict(r) -> dict:
    """参照メモ（RetrievedMemo）を履歴保存用の軽量 dict に変換する。"""
    m = r.memo
    try:
        created = m.created_at.astimezone().strftime("%Y/%m/%d")
    except Exception:
        created = str(m.created_at)[:10]
    return {
        "company_name": m.company_name,
        "memo_type": m.memo_type,
        "created": created,
        "score": round(float(r.score), 2),
    }


def _format_reference_line(r) -> str:
    """参照情報1件を Markdown 行に整形する。

    r は次のいずれか:
      - {"line": "長期記憶：志望職種=営業"} のような参考情報 dict
      - {"company_name","memo_type","created"} のメモ dict
      - RetrievedMemo
    """
    if isinstance(r, dict):
        if "line" in r:
            return f"- {r['line']}"
        company = r.get("company_name", "不明")
        mtype = r.get("memo_type", "")
        created = r.get("created", "")
        return f"- **{company}**（{mtype} / {created}）"
    else:
        m = r.memo
        try:
            created = m.created_at.astimezone().strftime("%Y/%m/%d")
        except Exception:
            created = str(m.created_at)[:10]
        return f"- **{m.company_name}**（{m.memo_type} / {created}）"


# ─── エントリーポイント ───────────────────────────────────────────────────────

def main() -> None:
    # 設定チェック：未設定項目があればセットアップ案内画面を表示して終了
    is_ready, missing = check_settings()
    if not is_ready:
        show_setup_guide(missing)
        return

    # サイドバーの登録フォーム（接続確認は企業研究シートで行う）
    base_sheets, base_error = get_sheets("企業研究")
    sidebar_form(base_sheets, base_error)

    # ── ページ切替（ホーム / お気に入り / メモ / 分析） ──────────────────────
    active_page = get_current_page()
    # URL の ?page= を現在のページに同期する。
    # これにより、自動更新の location.reload()（session_state が消える全リロード）
    # の後でも、同じページに正しく着地できる。値が変わらない限り再実行では
    # 遷移は起きない（get_current_page の変化検出により保証）。
    try:
        if st.query_params.get("page") != active_page:
            st.query_params["page"] = active_page
    except Exception:
        pass
    # 画面上部の共通ページ切替（PC でも操作可能。モバイルでは縦並びになる）
    render_page_selector(active_page)
    st.markdown("<hr class='section'>", unsafe_allow_html=True)

    # ── ルーティング ─────────────────────────────────────────────────────
    if active_page == "agent":
        agent_page()
    elif active_page == "favorites":
        favorites_page()
    elif active_page == "memo":
        memo_page()
    elif active_page == "analytics":
        analytics_page()
    else:
        # home: 一覧は全シート（企業研究/ES・履歴書/面接/インターン/選考結果）を集約表示。
        # 種別での絞り込みはメインエリア内の「メモ種別」フィルタで行える。
        main_area()

    # ── モバイル下部ナビ（CSS によりモバイル時のみ表示される） ───────────────
    render_mobile_bottom_nav(active_page)


if __name__ == "__main__":
    main()
