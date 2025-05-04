# ============================
# build.py - T2-Pressのメインビルドスクリプト
# Notionからデータを取得し、静的HTMLサイトを生成する
# ============================

# --- 必要な標準ライブラリをインポート ---
import os
import logging  # ログ出力用  # OS関連の操作（環境変数取得など）
import re  # 正規表現操作
import shutil  # ファイル・ディレクトリ操作（コピー、削除など）
import yaml  # YAML形式の設定ファイルを読み込むため
import argparse  # コマンドライン引数解析
import webbrowser  # ブラウザを自動起動するため
import threading  # ブラウザ起動とHTTPサーバーを並行実行するため
from pathlib import Path  # パス操作を簡素に行う（cross-platform対応）
from dotenv import load_dotenv  # .envファイルから環境変数を読み込む
import datetime  # 日時処理
from http.server import HTTPServer, SimpleHTTPRequestHandler  # ローカルサーバー構築用

# --- 外部ライブラリ（要インストール） ---
from notion_client import Client, APIResponseError  # Notion APIクライアントとエラー型
from jinja2 import Environment, FileSystemLoader  # テンプレートエンジンJinja2
from pykakasi import kakasi  # 日本語→ローマ字変換ライブラリ（スラッグ生成用）
import requests  # HTTPリクエスト

# --- シンタックスハイライト用ライブラリ ---
from pygments import highlight  # ハイライト処理
from pygments.lexers import get_lexer_by_name, TextLexer  # 言語ごとのパーサー
from pygments.formatters import HtmlFormatter  # HTML用のフォーマッタ

# --- 定数設定 ---
VERBOSE = False  # 通常は詳細ログ（DEBUG）は非表示にする

# --- ロガー初期化 ---
logging.basicConfig(
    level=logging.DEBUG if VERBOSE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"  # サイト基本設定ファイルのパス
THEME_CONFIG_PATH_TEMPLATE = "themes/{}/config.yaml"  # テーマごとの設定ファイルパス

# --- YAML設定ファイルを安全に読み込む関数 ---
def load_yaml(path):
    """
    YAMLファイルを読み込んで辞書として返す
    ファイルが存在しないか、読み込みに失敗した場合は空の辞書を返す
    """
    try:
        if not Path(path).exists():
            logger.info(f"⚠️ YAMLファイルが見つかりません: {path}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.info(f"❌ YAML読み込み失敗: {path} ({e})")
        return {}

# --- ディレクトリを安全に作成する関数 ---
def safe_mkdir(path):
    """
    指定ディレクトリを作成。既に存在していてもエラーを出さない。
    """
    path.mkdir(parents=True, exist_ok=True)

# --- 環境変数（Notion APIキーなど）の読み込み ---
load_dotenv()  # .env ファイルから環境変数をロード
NOTION_TOKEN = os.getenv("NOTION_TOKEN")  # Notion API トークン
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")  # データベースID

# 環境変数が設定されていなければ即終了
if not NOTION_TOKEN or not NOTION_DATABASE_ID:
    logger.error("❌ 環境変数 NOTION_TOKEN または NOTION_DATABASE_ID が未設定です")
    exit(1)

# --- サイト設定・テーマ設定の読み込み ---
config = load_yaml(CONFIG_PATH)
SITE_NAME = config.get("site_name", "T2-Press")
BASE_URL = config.get("base_url", "/")
THEME = config.get("theme", "default")
OUTPUT_DIR = Path(config.get("output_dir", "_output"))

THEME_CONFIG_PATH = THEME_CONFIG_PATH_TEMPLATE.format(THEME)
theme_config = load_yaml(THEME_CONFIG_PATH)

# Jinja2用の共通コンテキスト生成
render_context = {**config, "theme": theme_config}

# --- Notion APIクライアントとローマ字変換初期化 ---
notion = Client(auth=NOTION_TOKEN)
kakasi_inst = kakasi()  # 日本語→ローマ字の変換器

# --- RichText配列をHTML文字列に変換する関数 ---
def render_rich_text(texts):
    """
    Notionのrich_textリストをHTMLタグ付き文字列に変換する
    bold, italic, code, link などの装飾を適用
    """
    result = []
    for t in texts:
        txt = t.get("plain_text", "")
        ann = t.get("annotations", {})
        href = t.get("href")

        if ann.get("code"): txt = f"<code>{txt}</code>"
        if ann.get("bold"): txt = f"<strong>{txt}</strong>"
        if ann.get("italic"): txt = f"<em>{txt}</em>"
        if ann.get("strikethrough"): txt = f"<del>{txt}</del>"
        if ann.get("underline"): txt = f"<u>{txt}</u>"
        if href: txt = f'<a href="{href}">{txt}</a>'

        result.append(txt)
    return "".join(result)

# --- タイトル文字列からURLスラッグを生成 ---
def to_slug(text):
    """
    日本語のタイトルをローマ字に変換し、URL向けのスラッグに変換
    例: "今日の天気" → "kyou-no-tenki"
    """
    conv = kakasi_inst.convert(text)
    raw = "".join([t["hepburn"] for t in conv])
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw.lower()).strip("-") or "post"

# --- リスト型ブロック（箇条書き・番号付き）をHTMLに変換する関数 ---
def render_list_block(block, notion):
    """
    Notionのリストアイテム（bulleted_list_item / numbered_list_item）をHTMLの<li>形式に変換。
    子要素を持つ場合は再帰的に<ul>または<ol>を構築。
    """
    btype = block["type"]  # ブロックの型（bulleted_list_item など）
    tag = "ul" if btype == "bulleted_list_item" else "ol"  # HTMLタグを決定
    text = render_rich_text(block[btype].get("rich_text", []))  # リッチテキストをHTMLに変換
    html = f"<li>{text}"  # <li>タグ開始

    # 子要素がある場合、再帰的にリストを生成
    if block.get("has_children"):
        children = notion.blocks.children.list(block_id=block["id"])['results']
        if children:
            html += f"\n<{tag}>"  # ネストされた<ul> or <ol>を開く
            for child in children:
                html += render_list_block(child, notion)  # 再帰呼び出し
            html += f"</{tag}>"  # ネスト終了
    return html + "</li>\n"  # <li>閉じて返す

# --- 外部サービス（YouTubeなど）のURLを埋め込み可能な形式に変換 ---
def convert_embed_url(url: str) -> str:
    """
    指定されたURLを iframe で埋め込み可能な形式に変換する。
    現在の対応サービス:
    - YouTube（youtu.be, youtube.com）
    - ニコニコ動画
    """
    # YouTube（短縮URL）
    if "youtu.be" in url:
        video_id = url.split("/")[-1]
        return f"https://www.youtube.com/embed/{video_id}"
    
    # YouTube（標準URL）
    elif "youtube.com/watch?v=" in url:
        video_id = url.split("v=")[-1].split("&")[0]
        return f"https://www.youtube.com/embed/{video_id}"
    
    # ニコニコ動画
    if "nicovideo.jp/watch/" in url:
        video_id = url.split("/")[-1]
        return f"https://embed.nicovideo.jp/watch/{video_id}"
    
    # 対応外 → そのまま返す
    return url

# --- 任意のNotionブロックをHTMLに変換 ---
def render_block_html(block, notion):
    """
    ブロックタイプに応じたHTML出力を行う
    - 見出し、段落、引用、コード、リスト、チェックボックス、表、画像、動画など
    - サポートされないタイプは警告を出してデフォルト表示
    """
    btype = block["type"]  # ブロックの型
    data = block.get(btype, {})  # データ本体
    text = render_rich_text(data.get("rich_text", []))  # リッチテキスト変換

    if btype == "paragraph":
        return f"<p>{text}</p>"

    if btype.startswith("heading_"):
        # heading_1 / heading_2 / heading_3 → <h1>〜<h3>
        level = btype[-1]  # 末尾の数字を取得
        return f'<h{level}>{text}</h{level}>'

    if btype == "quote":
        return f"<blockquote>{text}</blockquote>"

    if btype == "code":
        lang = data.get("language", "").strip().lower()
        logger.info(f"💡 コードブロック言語: '{lang}'")

        # コード本文をプレーンテキストとして連結
        code_text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))

        # シンタックスハイライトに使用するlexerを取得（対応言語でなければTextLexerにフォールバック）
        try:
            lexer = get_lexer_by_name(lang) if lang else TextLexer()
        except Exception:
            logger.info(f"⚠️ 未対応の言語 '{lang}' → fallback to TextLexer")
            lexer = TextLexer()

        formatter = HtmlFormatter(nowrap=True)  # <span>だけ出力
        highlighted = highlight(code_text, lexer, formatter)
        return f'<div class="code-block"><pre>{highlighted}</pre></div>'

    if btype in ["bulleted_list_item", "numbered_list_item"]:
        return render_list_block(block, notion)

    if btype == "to_do":
        # チェック済みなら checked 属性を追加
        checked = "checked" if data.get("checked", False) else ""
        return f'<div class="checkbox"><label><input type="checkbox" disabled {checked}> {text}</label></div>'

    if btype == "table":
        # 子要素（行）を取得
        rows = notion.blocks.children.list(block_id=block["id"])['results']
        table_html = "<table>"
        for i, row in enumerate(rows):
            cells = row.get("table_row", {}).get("cells", [])
            tag = "th" if i == 0 else "td"  # 1行目のみヘッダー扱い
            table_html += "<tr>" + "".join(f"<{tag}>{render_rich_text(cell)}</{tag}>" for cell in cells) + "</tr>"
        return table_html + "</table>"

    if btype == "embed":
        url = data.get("url", "")
        embed_url = convert_embed_url(url)
        return f'<iframe src="{embed_url}" title="埋め込み" frameborder="0" loading="lazy" allowfullscreen style="width:100%;height:400px;"></iframe>'

    if btype == "video":
        url = data.get("external", {}).get("url") or data.get("file", {}).get("url", "")
        embed_url = convert_embed_url(url)
        if any(x in embed_url for x in ["youtube.com", "nicovideo.jp"]):
            return f'<iframe src="{embed_url}" title="動画" frameborder="0" allowfullscreen loading="lazy" style="width:100%;height:400px;"></iframe>'
        return f'<video src="{url}" controls style="max-width: 100%; height: auto;"></video>'

    if btype == "divider":
        return "<hr>"

    if btype == "image":
        image_url = data.get("external", {}).get("url") or data.get("file", {}).get("url", "")
        caption = render_rich_text(data.get("caption", []))
        return f'<figure><img src="{image_url}" alt="{caption}" style="max-width:100%;"/><figcaption>{caption}</figcaption></figure>'

    # 未サポートブロック
    logger.info(f"⚠️ 未対応のブロックタイプ: {btype}")
    return f"<div>{text}</div>"

# --- Notionからデータを取得してHTMLを生成するメイン関数 ---
def notion_pull():
    """
    Notionデータベースから記事一覧を取得し、HTMLページに変換して出力ディレクトリに保存する。
    各記事は個別のフォルダに格納され、トップページ(index.html)も自動生成される。
    """

    logger.info("🚀 Notionデータの取得とHTML出力を開始します")

    # --- 出力ディレクトリの初期化 ---
    if OUTPUT_DIR.exists():
        logger.info(f"📁 既存の出力先 {OUTPUT_DIR} を削除します")
        shutil.rmtree(OUTPUT_DIR)  # 出力フォルダを丸ごと削除（クリーンビルド）
    OUTPUT_DIR.mkdir()  # 出力先のルートを作成
    (OUTPUT_DIR / "posts").mkdir(parents=True, exist_ok=True)  # 記事用フォルダを作成

    # --- static ファイルのコピー処理（テーマから） ---
    theme_static = Path(f"themes/{THEME}/static")
    output_static = OUTPUT_DIR / "static"

    if theme_static.exists():
        logger.info("📂 テーマ static ファイルをコピー中...")
        for item in theme_static.rglob("*"):
            rel_path = item.relative_to(theme_static)

            # .j2（テンプレート）ファイルはレンダリング、通常ファイルはコピー
            dest = output_static / rel_path.with_suffix('') if item.suffix == ".j2" else output_static / rel_path

            if item.is_dir():
                safe_mkdir(dest)
            elif item.suffix == ".j2":
                tpl = Environment(loader=FileSystemLoader(str(item.parent))).get_template(item.name)
                safe_mkdir(dest.parent)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(tpl.render(**render_context))
            else:
                safe_mkdir(dest.parent)
                shutil.copy2(item, dest)

    # --- src フォルダの中身をコピー（任意の静的素材） ---
    if Path("src").exists():
        logger.info("📦 src ディレクトリをコピーします")
        shutil.copytree("src", OUTPUT_DIR / "src")

    # --- Jinja2 テンプレート読み込み（post, index） ---
    env = Environment(loader=FileSystemLoader(f"themes/{THEME}/templates"))
    tpl_post = env.get_template("post.html")
    tpl_index = env.get_template("index.html")

    # --- Notion API を使って記事一覧を取得 ---
    try:
        response = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={"property": "公開", "checkbox": {"equals": True}}  # 公開フラグがONのものだけ取得
        )
    except (APIResponseError, requests.exceptions.RequestException) as e:
        logger.info(f"❌ Notion APIエラー: {e}")
        raise

    posts_info = []  # トップページに表示する記事一覧用データ

    # --- 各記事をHTMLとして生成 ---
    for page in response["results"]:
        props = page["properties"]

        # 記事タイトル取得（空なら「無題」）
        title_data = props.get("記事", {}).get("title", [])
        title = title_data[0]["plain_text"] if title_data else "無題"

        # スラッグ生成
        slug = to_slug(title)
        page_id = page["id"]

        logger.info(f"📝 記事: {title} (slug: {slug})")

        # 記事本文（ブロック）を取得
        blocks = notion.blocks.children.list(block_id=page_id)["results"]

        html_blocks = []
        current_list_tag = None  # リストの開閉管理用（ul/ol）

        for block in blocks:
            btype = block["type"]
            if btype in ["bulleted_list_item", "numbered_list_item"]:
                tag = "ul" if btype == "bulleted_list_item" else "ol"

                # リストのタグが切り替わったら閉じて開く
                if current_list_tag != tag:
                    if current_list_tag:
                        html_blocks.append(f"</{current_list_tag}>")
                    html_blocks.append(f"<{tag}>")
                    current_list_tag = tag

                html_blocks.append(render_list_block(block, notion))

            else:
                # リストの途中で他のブロックが来たら閉じる
                if current_list_tag:
                    html_blocks.append(f"</{current_list_tag}>")
                    current_list_tag = None

                html_blocks.append(render_block_html(block, notion))

        # 最後にリストが開いたままなら閉じる
        if current_list_tag:
            html_blocks.append(f"</{current_list_tag}>")

        # 記事のHTMLコンテンツを連結
        content_html = "\n".join(html_blocks)

        # 出力先フォルダを作成してHTMLファイルを書き出し
        post_dir = OUTPUT_DIR / "posts" / slug
        safe_mkdir(post_dir)
        with open(post_dir / "index.html", "w", encoding="utf-8") as f:
            f.write(tpl_post.render(title=title, content=content_html, **render_context))

        posts_info.append({"title": title, "url": f"posts/{slug}/index.html"})

    # --- トップページ（記事一覧）を出力 ---
    with open(OUTPUT_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(tpl_index.render(posts=posts_info, **render_context))

    logger.info("✅ サイト生成完了")

# --- ローカル開発用サーバー起動関数 ---
def serve():
    """
    ビルド済みのHTMLサイトをローカルで確認できるよう、HTTPサーバーを起動する。
    デフォルトでは http://localhost:8000 で起動。
    ブラウザ自動起動オプション付き。
    """

    # --- コマンドライン引数の解析 ---
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)  # ポート番号の指定（任意）
    parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = parser.parse_args()

    # --- サーバーのカレントディレクトリを出力ディレクトリに移動 ---
    os.chdir(OUTPUT_DIR)

    # --- サーバーインスタンス作成 ---
    server = HTTPServer(("localhost", args.port), SimpleHTTPRequestHandler)
    url = f"http://localhost:{args.port}"

    print(f"🌐 サーバー起動: {url}")
    print("⛔ Ctrl+Cで停止")

    # --- ブラウザを自動で開く（--no-browser が指定されていない場合） ---
    if not args.no_browser:
        threading.Thread(
            target=lambda: webbrowser.open(url),  # 新しいスレッドでブラウザ起動（非同期）
            daemon=True  # メインスレッドに依存（終了時に一緒に終わる）
        ).start()

    # --- サーバー開始（Ctrl+Cで停止） ---
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("🛑 サーバーを停止しました。")

# --- メイン実行処理（スクリプトとして直接実行された場合） ---
if __name__ == "__main__":
    """
    実行手順:
    1. Notionからデータを取得してHTMLを生成（notion_pull）
    2. ローカルサーバーを起動して確認用ページを提供（serve）
    """

    try:
        notion_pull()  # サイトビルド（Notion → HTML生成）
    except (APIResponseError, requests.exceptions.RequestException):
        # Notion API通信に失敗した場合は静かに終了
        exit(1)

    serve()  # サーバー起動（http://localhost:8000）
