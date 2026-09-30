"""
AIサービス: Google Gemini API を使ってメモを分類・構造化する

1回の API 呼び出しで以下を生成する:
  1. 企業名の推定
  2. メモ種別の自動判定（企業研究 / 面接 / ES / インターン / その他）
  3. detailed_summary : 見出し付きの詳細な構造化要約（Markdown・Streamlit 表示用）
  4. sections         : セクション名→要点(50字以内) の辞書（Google Sheets 列分割用）
  5. keywords         : キーワード抽出（3〜10個）
  6. next_action      : 次回アクションの提案
"""

from __future__ import annotations

import json
import logging
import traceback

import google.generativeai as genai

from config import settings
from models.memo import (
    AnalysisResult,
    MEMO_TYPES,
    CATEGORIES,
    SECTIONS_BY_TYPE,
)

logger = logging.getLogger(__name__)


# ─── 例外 ─────────────────────────────────────────────────────────────────────

class AIAnalysisError(Exception):
    """AI分析が失敗したことを呼び出し元に伝える例外。

    API エラー（モデル未検出・認証・レート制限など）で発生する。
    サイレントにフォールバックせず、UI にエラーを表示するために使う。

    UI で原因を分かりやすく出し分けられるよう、構造化した情報を保持する:
        - title:       原因の見出し（例:「1日の無料利用上限に達しました」）
        - guidance:    ユーザーが取るべき対処（1〜複数行）
        - kind:        機械判定用の種類コード
                       ("rate_limit_daily" / "rate_limit_minute" / "quota" /
                        "auth" / "model" / "network" / "timeout" / "unknown")
        - retry_after: 再試行までの推奨待機秒数（分かる場合のみ / int）
        - raw_detail:  元の例外メッセージ（詳細表示・デバッグ用）
    """

    def __init__(
        self,
        title: str,
        guidance: str = "",
        *,
        kind: str = "unknown",
        retry_after: int | None = None,
        raw_detail: str = "",
    ) -> None:
        self.title = title
        self.guidance = guidance
        self.kind = kind
        self.retry_after = retry_after
        self.raw_detail = raw_detail
        # 従来通り str(err) でも意味が通るようにしておく
        message = title if not guidance else f"{title}\n{guidance}"
        super().__init__(message)


# ─── API エラー分類 ───────────────────────────────────────────────────────────

import re as _re


def _fmt_retry(seconds: int | None) -> str:
    """再試行待機秒数を「約N秒後」「約N分後」の日本語に整形する。"""
    if not seconds or seconds <= 0:
        return ""
    if seconds < 90:
        return f"約{seconds}秒後"
    minutes = round(seconds / 60)
    return f"約{minutes}分後"


def _parse_retry_after(text: str) -> int | None:
    """
    エラーメッセージから再試行までの秒数を抽出する。

    以下の両方に対応:
        "Please retry in 30.64294093s."
        "retry_delay { seconds: 30 }"
    """
    m = _re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s", text, _re.IGNORECASE)
    if m:
        return int(float(m.group(1))) + 1  # 端数は切り上げ気味に
    m = _re.search(r"retry_delay\s*\{\s*seconds:\s*([0-9]+)", text)
    if m:
        return int(m.group(1))
    return None


def _classify_api_error(exc: Exception) -> AIAnalysisError:
    """
    Gemini API 呼び出しで発生した例外を分類し、原因が分かる AIAnalysisError に変換する。

    レート制限（1日 / 1分）・課金/クォータ超過・認証エラー・モデル名誤り・
    ネットワーク/タイムアウトを、メッセージとステータスコードから判別する。
    """
    detail = str(exc)
    low = detail.lower()
    type_name = type(exc).__name__

    # ステータスコードを可能なら取得（google.api_core 例外は .code を持つことがある）
    status = None
    for attr in ("code", "status_code", "grpc_status_code"):
        val = getattr(exc, attr, None)
        if val is not None:
            status = getattr(val, "value", val)
            break

    retry_after = _parse_retry_after(detail)

    is_429 = (
        status == 429
        or "429" in detail
        or "resourceexhausted" in type_name.lower()
        or "resource_exhausted" in low
        or "exceeded your current quota" in low
        or "quota" in low and "exceeded" in low
    )
    if is_429:
        # 1日あたり（PerDay）か 1分あたり（PerMinute）かを判別
        is_daily = (
            "perday" in low
            or "per day" in low
            or "requestsperday" in low
            or "free_tier_requests" in low  # 無料枠の1日上限でよく出るメトリクス
        )
        is_minute = (
            "perminute" in low
            or "per minute" in low
            or "requestsperminute" in low
        )
        wait = _fmt_retry(retry_after)

        if is_daily and not is_minute:
            return AIAnalysisError(
                title="🚦 1日あたりの無料利用上限に達しました",
                guidance=(
                    "Gemini API の無料枠（1日あたりのリクエスト数）を使い切りました。\n"
                    "・日付が変わる（太平洋時間の0時＝日本時間の 16〜17時頃）まで待つと回復します。\n"
                    "・すぐ使いたい場合は、Google Cloud で課金を有効にすると上限が大きく引き上げられます。\n"
                    "・別の API キー / プロジェクトに切り替える方法もあります。"
                ),
                kind="rate_limit_daily",
                retry_after=retry_after,
                raw_detail=detail,
            )
        if is_minute:
            base = "⏳ 短時間にリクエストを送りすぎました（1分あたりの上限）"
            g = (
                "1分あたりの送信回数の上限に一時的に達しました。\n"
                f"・{wait or '少し時間をおいて'}もう一度お試しください。\n"
                "・連続して登録する場合は、数秒あけると安定します。"
            )
            return AIAnalysisError(
                title=base,
                guidance=g,
                kind="rate_limit_minute",
                retry_after=retry_after,
                raw_detail=detail,
            )
        # 日/分の判別ができないが 429 → 汎用のレート/クォータ超過
        g = (
            "APIの利用上限（レート制限またはクォータ）に達しました。\n"
            f"・{wait or 'しばらく時間をおいて'}再試行してください。\n"
            "・繰り返し出る場合は、無料枠の1日上限に達している可能性があります"
            "（課金の有効化で解消します）。"
        )
        return AIAnalysisError(
            title="🚦 APIの利用上限（レート制限）に達しました",
            guidance=g,
            kind="quota",
            retry_after=retry_after,
            raw_detail=detail,
        )

    # 認証エラー（401 / 403 / APIキー無効）
    is_auth = (
        status in (401, 403)
        or "unauthenticated" in type_name.lower()
        or "permissiondenied" in type_name.lower()
        or "api key not valid" in low
        or "api_key_invalid" in low
        or "permission denied" in low
        or "unauthorized" in low
    )
    if is_auth:
        return AIAnalysisError(
            title="🔑 APIキーが無効か、権限がありません",
            guidance=(
                "`GEMINI_API_KEY` が正しくないか、無効化・期限切れの可能性があります。\n"
                "・`.env` のキーが `AIza` から始まる正しい値か確認してください。\n"
                "・Google AI Studio でキーを再発行して差し替えてください。"
            ),
            kind="auth",
            raw_detail=detail,
        )

    # モデル名の誤り（404 / 400 + model not found）
    is_model = (
        status in (400, 404)
        and ("model" in low)
        or "not found" in low and "model" in low
        or "is not found for api version" in low
        or "invalid model" in low
    )
    if is_model:
        return AIAnalysisError(
            title="🧩 指定されたAIモデルが利用できません",
            guidance=(
                f"モデル名 `{settings.gemini_model}` が正しくないか、"
                "現在のアカウントでは利用できません。\n"
                "・`.env` の `GEMINI_MODEL` を有効なモデル名に修正してください。\n"
                "・`gemini-flash-latest` など提供中のモデルを指定してください。"
            ),
            kind="model",
            raw_detail=detail,
        )

    # ネットワーク / タイムアウト
    is_network = (
        "deadline" in low
        or "timeout" in low
        or "timed out" in low
        or "connection" in low
        or "unavailable" in low
        or "temporarily" in low
        or status in (500, 502, 503, 504)
    )
    if is_network:
        return AIAnalysisError(
            title="🌐 ネットワークまたはサーバー側の一時的なエラーです",
            guidance=(
                "通信エラー、またはGemini側の一時的な混雑・障害の可能性があります。\n"
                "・ネットワーク接続を確認し、少し時間をおいて再試行してください。"
            ),
            kind="network",
            raw_detail=detail,
        )

    # 上記に当てはまらない未分類のエラー
    return AIAnalysisError(
        title="❓ AI分析中に予期しないエラーが発生しました",
        guidance=(
            f"モデル: `{settings.gemini_model}`\n"
            "・時間をおいて再試行してください。\n"
            "・繰り返し発生する場合は、下の詳細を確認してください。"
        ),
        kind="unknown",
        retry_after=retry_after,
        raw_detail=detail,
    )


# ─── プロンプト ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
あなたは就職活動メモの整理AIです。
入力されたメモを分析し、後から活用できる構造化データとして整理してください。
必ず指定の JSON 形式のみで返答し、JSON 以外は一切出力しないでください。
"""

_USER_PROMPT_TEMPLATE = """\
## ヒント情報（省略可）
{hints}

## メモ本文
{raw_text}

---

## 出力 JSON 仕様

以下の JSON を厳密に守って返答してください。

{{
  "company_name": "企業名（本文から読み取れない場合は '不明'）",
  "category": "次のいずれか1つ: {categories}",
  "memo_type": "次のいずれか1つ: {memo_types}",
  "keywords": ["キーワード1", "キーワード2", ... ],
  "next_action": "次回に行うべき具体的なアクション（1〜2文）",
  "sections": {{ "セクション名": "要点(50字以内)", ... }},
  "detailed_summary": "見出し付きMarkdownの詳細要約（\\nでエスケープ）"
}}

## memo_type の分類ルール（重要）

memo_type は必ず次の5種類のいずれか1つにしてください。「その他」は使わないでください。
判断に迷う場合も、最も近い1つを必ず選んでください。

- 「企業研究」: 企業概要・事業内容・強み・求める人物像・社風・説明会内容・
  OB/OG訪問内容・社員との会話・（自分がした）質問と回答・志望度 などを含むメモ
- 「ES・履歴書」: ES・履歴書・自己PR・ガクチカ・志望動機（の文章作成）・添削内容 などを含むメモ
- 「面接」: 面接での質問・自分の回答・面接官の反応・面接の振り返り・逆質問・改善点 などを含むメモ
- 「インターン」: インターン参加記録・プログラム内容・グループワーク・社員交流・
  選考優遇情報 などを含むメモ
- 「選考結果」: 通過・落選・辞退・結果通知・選考の振り返り などを含むメモ

## メモ種別ごとのセクション項目

memo_type によって sections のキー（セクション名）は以下を使ってください。

{type_sections_spec}

## sections の書き方（Google Sheets 列分割用・一覧性重視）

- キーは上記のセクション名をそのまま使う
- 値は **50文字以内・1〜3行以内**の要点のみ
- 箇条書きや体言止め中心。長文説明・背景説明・修飾語は禁止
- 情報がないセクションは省略してよい（キーごと出さない）

### sections の良い例（企業研究）
{{
  "企業概要": "IT系SIer、1985年設立",
  "事業内容": "金融・官公庁向け開発",
  "強み・特徴": "官公庁案件に強み",
  "社風・社員": "若手活躍・挑戦重視"
}}

### memo_type=企業研究 のときの補足
- "質問内容": 説明会・企業研究で自分が質問した内容（あれば）
- "回答内容": その質問に対して得られた回答・情報
- "志望度": メモ全体のトーンから推定し、必ず「高」「中」「低」のいずれか1文字で出力
- 各値は50文字以内を厳守

### memo_type=面接 のときの補足
- "面接日": 本文から読み取れれば YYYY-MM-DD 形式（年が不明なら MM-DD、無ければ省略）
- "面接段階": 「一次面接」「二次面接」「最終面接」「カジュアル面談」等の短い語
- "質問内容": 聞かれた質問を箇条書きで簡潔に
- "自分の回答": 回答の要点
- "逆質問": 自分から面接官に質問した内容（あれば）
- 各値は50文字以内を厳守

### memo_type=インターン のときの補足
- "開催日": 本文から読み取れれば YYYY-MM-DD 形式（無ければ省略）
- "開催形式": 「対面」「オンライン」「ハイブリッド」等の短い語
- "社員への質問"/"社員の回答": 座談会等で聞いた内容と回答の要点
- "選考優遇情報": 早期選考・優遇ルート・選考免除などの案内があればその要点。
  早期選考の案内があれば「有: 〈時期・方法・条件〉」の形で先頭に明記する。
  例）「有: 12月上旬に一次面接免除の案内メール」「有: 参加者限定で1月選考、ES免除」「無」「案内なし」
- "本選考への影響": 参加が本選考にどう影響するか（優遇・直結・関係なし等）の要点
- 各値は50文字以内を厳守

### memo_type=ES のときの補足
- "提出日": 本文から読み取れれば YYYY-MM-DD 形式（無ければ省略）
- "設問": ES/履歴書の設問文の要点
- "設問種別": 「志望動機」「自己PR」「ガクチカ」「長所短所」等の短い語
- "提出文章": 提出した内容の要点（全文ではなく50字以内の要約）
- "想定質問": この ES から面接で聞かれそうな質問
- 各値は50文字以内を厳守

### memo_type=選考結果 のときの補足
- "選考段階": 「書類選考」「一次面接」「最終面接」「内定」等の短い語
- "結果": 「通過」「合格」「不合格」「内定」「辞退」等の短い語
- "通知日": 本文から読み取れれば YYYY-MM-DD 形式（無ければ省略）
- "通過要因"/"落選要因": 結果に至ったと考えられる要因の要点
- "学び": 次の選考に活かせる気づき
- 各値は50文字以内を厳守

### 悪い例（長すぎ・禁止）
"強み・特徴": "官公庁案件を長年手掛けており豊富な実績と顧客基盤を持つ..."

## detailed_summary の書き方（Streamlit 詳細表示用）

- sections と同じ情報を、読みやすい詳細版として整理する
- 各セクションを「## セクション名」の Markdown 見出しにする
- 見出しの下に「・」で始まる箇条書きを並べる
- sections より詳しく書いてよい（背景や補足を含めてよい）
- 情報がなければ「・（記載なし）」とする

### detailed_summary の例
## 企業概要\\n・IT系SIer\\n・1985年設立\\n\\n## 事業内容\\n・金融システム開発\\n・DX支援

## 共通ルール
- company_name: 固有名詞を優先。「株式会社」「(株)」等の表記を統一。
- category: 内容から最も近いものを1つだけ選ぶ。
- memo_type: 内容から最も近いものを1つだけ選ぶ。
- keywords: 3〜10個、就活で検索・参照しやすい簡潔な名詞で。
- next_action: 「〇〇を確認する」「〇〇を準備する」など行動可能な表現で。
- JSON の文字列内の改行は \\n でエスケープする。
"""


def _build_type_sections_spec() -> str:
    """全種別のセクション項目一覧をプロンプト用に整形する"""
    blocks = []
    for t, items in SECTIONS_BY_TYPE.items():
        headers = "、".join(f"「{h}」" for h in items)
        blocks.append(f"- memo_type={t} の場合: {headers}")
    return "\n".join(blocks)


# ─── Gemini クライアント（遅延初期化） ───────────────────────────────────────

_model: genai.GenerativeModel | None = None


def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        if not settings.gemini_api_key:
            raise AIAnalysisError(
                title="🔑 APIキーが設定されていません",
                guidance=(
                    "`GEMINI_API_KEY` が未設定です。\n"
                    "・プロジェクトルートの `.env` に `GEMINI_API_KEY=AIza...` を設定してください。\n"
                    "・設定後はアプリを再起動してください。"
                ),
                kind="auth",
            )
        genai.configure(api_key=settings.gemini_api_key)
        _model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=8192,   # detailed_summary が長くなるため十分に確保
            ),
        )
    return _model


# ─── OCR（画像→テキスト化）────────────────────────────────────────────────
# 入力補助機能。ここでは「画像内の文字をそのままテキスト化」するだけで、
# 要約・分類・タグ付けは一切行わない（それらは既存の analyze_memo に任せる）。
# 生成した OCR テキストは UI 上でユーザーが確認・編集してから、
# 既存のメモ登録フロー（analyze_memo → Sheets 保存）へ渡す。

_ocr_model: genai.GenerativeModel | None = None

# OCR 専用プロンプト（要約・解釈をせず、書かれている内容をそのまま出力させる）
_OCR_PROMPT = (
    "画像内の文字を可能な限り正確にテキスト化してください。\n"
    "要約や解釈は行わず、書かれている内容をそのまま出力してください。\n"
    "レイアウト（改行・箇条書き・見出し）はできるだけ元の見た目を保ってください。\n"
    "文字が読み取れない箇所は無理に推測せず、その部分は空欄のままにしてください。\n"
    "説明文や前置きは付けず、本文テキストだけを出力してください。"
)


def _get_ocr_model() -> genai.GenerativeModel:
    """OCR 用の Gemini モデルを取得する（プレーンテキスト出力・低温度で正確性重視）。

    既存の API キー・モデル名（settings）をそのまま再利用する。
    analyze_memo 用の _get_model は JSON 出力設定のため、OCR では別インスタンスを使う。
    """
    global _ocr_model
    if _ocr_model is None:
        if not settings.gemini_api_key:
            raise AIAnalysisError(
                title="🔑 APIキーが設定されていません",
                guidance=(
                    "`GEMINI_API_KEY` が未設定です。\n"
                    "・プロジェクトルートの `.env` に `GEMINI_API_KEY=AIza...` を設定してください。\n"
                    "・設定後はアプリを再起動してください。"
                ),
                kind="auth",
            )
        genai.configure(api_key=settings.gemini_api_key)
        _ocr_model = genai.GenerativeModel(
            model_name=settings.gemini_model,   # 既存のモデル設定を再利用
            generation_config=genai.GenerationConfig(
                response_mime_type="text/plain",  # OCR はプレーンテキストで受け取る
                temperature=0.0,                   # 正確性最優先（創作を避ける）
                max_output_tokens=8192,
            ),
        )
    return _ocr_model


def extract_text_from_image(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """画像を Gemini に渡し、画像内の文字をそのままテキスト化して返す（OCR）。

    要約・分類・タグ付けは行わない（入力補助に徹する）。返したテキストは、
    呼び出し側でユーザーが確認・編集したうえで analyze_memo に渡す想定。

    Args:
        image_bytes: 画像のバイナリ。
        mime_type:   画像の MIME タイプ（例: image/png, image/jpeg）。

    Returns:
        OCR で読み取ったテキスト（前後の空白は除去）。

    Raises:
        AIAnalysisError: Gemini API 呼び出しが失敗した場合（原因別に分類）。
    """
    logger.info("[OCR] extract_text_from_image: model=%s mime=%s bytes=%d",
                settings.gemini_model, mime_type, len(image_bytes))
    try:
        response = _get_ocr_model().generate_content([
            _OCR_PROMPT,
            {"mime_type": mime_type, "data": image_bytes},
        ])
        text = (response.text or "").strip()
        logger.info("[OCR] 完了: 文字数=%d", len(text))
        return text
    except AIAnalysisError:
        raise
    except Exception as e:
        logger.error("[OCR] Gemini 呼び出し失敗:\n%s", traceback.format_exc())
        # analyze_memo と同じエラー分類を再利用（レート制限・認証・モデル等）
        raise _classify_api_error(e) from e


# ─── メイン関数 ───────────────────────────────────────────────────────────────

def analyze_memo(
    raw_text: str,
    company_hint: str | None = None,
    category_hint: str | None = None,
) -> AnalysisResult:
    """
    メモ本文を受け取り、種別判定・詳細要約・セクション・キーワード・次回アクションを返す。

    Raises:
        AIAnalysisError: Gemini API 呼び出しが失敗した場合。
    """
    prompt = _build_prompt(raw_text, company_hint, category_hint)
    logger.info("Gemini analyze_memo: model=%s len=%d", settings.gemini_model, len(raw_text))

    last_json_error: Exception | None = None
    last_content: str = ""
    hit_max_tokens = False

    for attempt in range(1, 3):
        try:
            response = _get_model().generate_content(prompt)
            content = response.text or "{}"
        except AIAnalysisError:
            raise
        except Exception as e:
            logger.error(
                "[AI] Gemini API 呼び出し失敗 (model=%s):\n%s",
                settings.gemini_model, traceback.format_exc(),
            )
            # 例外を原因別に分類し、ユーザーに分かる形の AIAnalysisError にする
            raise _classify_api_error(e) from e

        last_content = content

        # finish_reason を確認（2 = MAX_TOKENS = 出力途中で打ち切り）
        finish_reason = _get_finish_reason(response)
        if finish_reason == 2:
            hit_max_tokens = True
            logger.warning(
                "[AI] finish_reason=MAX_TOKENS: 出力がトークン上限で打ち切られました "
                "（レスポンス長=%d）。部分サルベージを試みます。", len(content),
            )

        try:
            result = _parse_result(content)
            logger.info(
                "[AI] 解析完了: type=%s company=%s sections=%d件 keywords=%d件",
                result.memo_type, result.company_name,
                len(result.sections), len(result.keywords),
            )
            return result
        except (json.JSONDecodeError, ValueError) as e:
            last_json_error = e
            logger.warning(
                "[AI] JSON パース失敗 (attempt %d/2): %s\nレスポンス末尾: %s",
                attempt, e, content[-150:],
            )
            # MAX_TOKENS で切れた場合はリトライしても同じく切れるので即サルベージへ
            if hit_max_tokens:
                break
            continue

    # ── 部分サルベージ: 途中で切れた JSON から取れるフィールドだけ復元 ──────
    logger.warning("[AI] 部分サルベージを実行します: %s", last_json_error)
    salvaged = _salvage_partial(last_content)
    if salvaged is not None:
        salvaged.is_partial = True
        logger.info(
            "[AI] 部分サルベージ成功: type=%s company=%s sections=%d件（不完全フラグON）",
            salvaged.memo_type, salvaged.company_name, len(salvaged.sections),
        )
        return salvaged

    logger.error("[AI] サルベージも失敗。フォールバックします: %s", last_json_error)
    fb = _fallback_result(raw_text)
    fb.is_partial = True
    return fb


def _get_finish_reason(response) -> int | None:
    """Gemini レスポンスから finish_reason（int）を取り出す。取れなければ None。"""
    try:
        return int(response.candidates[0].finish_reason)
    except Exception:
        return None


# ─── プロンプト構築 ───────────────────────────────────────────────────────────

def _build_prompt(
    raw_text: str,
    company_hint: str | None,
    category_hint: str | None,
) -> str:
    hints_lines: list[str] = []
    if company_hint:
        hints_lines.append(f"- 企業名のヒント: {company_hint}")
    if category_hint:
        hints_lines.append(f"- カテゴリのヒント: {category_hint}")
    hints = "\n".join(hints_lines) if hints_lines else "（なし）"

    user_text = _USER_PROMPT_TEMPLATE.format(
        hints=hints,
        raw_text=raw_text,
        categories=" / ".join(CATEGORIES),
        memo_types=" / ".join(MEMO_TYPES),
        type_sections_spec=_build_type_sections_spec(),
    )
    return _SYSTEM_PROMPT + "\n\n" + user_text


# ─── パーサー ─────────────────────────────────────────────────────────────────

def _parse_result(content: str) -> AnalysisResult:
    data = json.loads(content)
    return _result_from_dict(data)


def _result_from_dict(data: dict) -> AnalysisResult:
    """パース済み（または部分復元済み）の dict から AnalysisResult を組み立てる"""
    memo_type = _validate_memo_type(data.get("memo_type", "その他"))

    # sections: dict[str, str]
    raw_sections = data.get("sections", {})
    sections: dict[str, str] = {}
    if isinstance(raw_sections, dict):
        for k, v in raw_sections.items():
            key = str(k).strip()
            val = str(v).strip()
            if key and val:
                sections[key] = val

    detailed_summary = str(data.get("detailed_summary", "")).strip()
    # detailed_summary が空なら sections から Markdown を組み立てる（保険）
    if not detailed_summary and sections:
        detailed_summary = _sections_to_markdown(sections)

    return AnalysisResult(
        company_name=str(data.get("company_name", "不明")).strip() or "不明",
        category=_validate_category(str(data.get("category", "その他"))),
        memo_type=memo_type,
        keywords=[str(k).strip() for k in data.get("keywords", []) if k],
        next_action=str(data.get("next_action", "")).strip(),
        detailed_summary=detailed_summary,
        sections=sections,
    )


def _salvage_partial(content: str) -> AnalysisResult | None:
    """
    途中で切れた（Unterminated string 等の）JSON から、
    取り出せるフィールドだけを復元して AnalysisResult を返す。

    戦略:
      1. 末尾を段階的に削って閉じ括弧を補い、json.loads を試みる
      2. それも無理なら正規表現でトップレベルの単純フィールドを個別抽出する
    """
    if not content:
        return None

    # ── 戦略1: 末尾を切り詰めて JSON として閉じ直す ──────────────────────
    for cut in range(len(content), max(len(content) - 4000, 0), -1):
        chunk = content[:cut]
        # 開いている文字列を閉じ、括弧の対応を補完する試み
        repaired = _try_close_json(chunk)
        if repaired is None:
            continue
        try:
            data = json.loads(repaired)
            if isinstance(data, dict) and data:
                logger.info("[AI] サルベージ: 末尾補修で %d 文字までパース成功", cut)
                return _result_from_dict(data)
        except (json.JSONDecodeError, ValueError):
            continue

    # ── 戦略2: 正規表現で個別フィールドを抽出 ────────────────────────────
    import re

    def _extract_str(field: str) -> str:
        m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
        if not m:
            return ""
        return m.group(1).encode().decode("unicode_escape", errors="ignore") \
            if "\\" in m.group(1) else m.group(1)

    company = _extract_str("company_name")
    memo_type = _extract_str("memo_type")
    next_action = _extract_str("next_action")

    # sections オブジェクト内の "key": "value" ペアを抽出
    sections: dict[str, str] = {}
    sec_block = re.search(r'"sections"\s*:\s*\{(.*?)(\}|$)', content, re.DOTALL)
    if sec_block:
        for k, v in re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', sec_block.group(1)):
            if k and v:
                sections[k.strip()] = v.strip()

    if not (company or sections or memo_type):
        return None

    logger.info("[AI] サルベージ: 正規表現で company=%s sections=%d件 抽出", company, len(sections))
    return _result_from_dict({
        "company_name": company or "不明",
        "memo_type": memo_type or "その他",
        "next_action": next_action,
        "sections": sections,
    })


def _try_close_json(chunk: str) -> str | None:
    """
    途中で切れた JSON 断片の未閉じ文字列・括弧を補って閉じる。
    補修できなければ None を返す。
    """
    s = chunk.rstrip()
    if not s:
        return None
    # 末尾がカンマや途中のキーなら、その手前まで戻す
    s = s.rstrip(",")

    # 文字列の途中で切れているか（未エスケープの " の個数が奇数）を判定
    in_string = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        s += '"'   # 開いている文字列を閉じる

    # 未閉じの { と [ を数えて閉じる
    depth_curly = s.count("{") - s.count("}")
    depth_square = s.count("[") - s.count("]")
    if depth_curly < 0 or depth_square < 0:
        return None
    s = s.rstrip(",")
    s += "]" * depth_square
    s += "}" * depth_curly
    return s


def _sections_to_markdown(sections: dict[str, str]) -> str:
    """sections 辞書から見出し付き Markdown を生成する（detailed_summary の保険）"""
    blocks = []
    for name, value in sections.items():
        body = value.replace("、", "\n・").replace("\n", "\n・")
        blocks.append(f"## {name}\n・{value}")
    return "\n\n".join(blocks)


def _validate_memo_type(value: str) -> str:
    if value in MEMO_TYPES:
        return value
    # 旧表記の後方互換（"ES" / "ES・履歴書"）
    if value in ("ES", "ES・履歴書", "履歴書"):
        return "ES・履歴書"
    for t in MEMO_TYPES:
        if t in value or value in t:
            return t
    # 5種に該当しない場合は最も汎用の「企業研究」に寄せる（「その他」は使わない）
    return "企業研究"


def _validate_category(value: str) -> str:
    if value in CATEGORIES:
        return value
    for cat in CATEGORIES:
        if cat in value or value in cat:
            return cat
    return "その他"


def _fallback_result(raw_text: str) -> AnalysisResult:
    """AI 処理が完全に失敗した場合のフォールバック"""
    preview = raw_text[:120].replace("\n", " ")
    return AnalysisResult(
        company_name="不明",
        category="その他",
        memo_type="企業研究",   # 5種に無い「その他」は使わない
        keywords=[],
        next_action="",
        detailed_summary=f"## 要約\n・{preview}",
        sections={"要約": preview[:50]},
    )
