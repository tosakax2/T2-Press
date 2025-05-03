import os
import re
import shutil
import yaml
import argparse
import webbrowser
import threading
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

from notion_client import Client, APIResponseError
from jinja2 import Environment, FileSystemLoader
from pykakasi import kakasi
import requests
import html

from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter


# --- 設定とユーティリティ ---
VERBOSE = True
CONFIG_PATH = "config.yaml"
THEME_CONFIG_PATH_TEMPLATE = "themes/{}/config.yaml"


def log(msg):
    if VERBOSE:
        print(msg)


def load_yaml(path):
    try:
        if not Path(path).exists():
            log(f"⚠️ YAMLファイルが見つかりません: {path}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        log(f"❌ YAML読み込み失敗: {path} ({e})")
        return {}


def safe_mkdir(path):
    path.mkdir(parents=True, exist_ok=True)


# --- 初期化 ---
load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not NOTION_TOKEN or not NOTION_DATABASE_ID:
    log("❌ 環境変数 NOTION_TOKEN または NOTION_DATABASE_ID が未設定です")
    exit(1)

config = load_yaml(CONFIG_PATH)
SITE_NAME = config.get("site_name", "T2-Press")
BASE_URL = config.get("base_url", "/")
THEME = config.get("theme", "default")
OUTPUT_DIR = Path(config.get("output_dir", "_output"))

THEME_CONFIG_PATH = THEME_CONFIG_PATH_TEMPLATE.format(THEME)
theme_config = load_yaml(THEME_CONFIG_PATH)
render_context = {**config, "theme": theme_config}

notion = Client(auth=NOTION_TOKEN)
kakasi_inst = kakasi()


# --- Notion Block Renderers ---
def render_rich_text(texts):
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


def to_slug(text):
    conv = kakasi_inst.convert(text)
    raw = "".join([t["hepburn"] for t in conv])
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw.lower()).strip("-") or "post"


def render_list_block(block, notion):
    btype = block["type"]
    tag = "ul" if btype == "bulleted_list_item" else "ol"
    text = render_rich_text(block[btype].get("rich_text", []))
    html = f"<li>{text}"

    if block.get("has_children"):
        children = notion.blocks.children.list(block_id=block["id"])['results']
        if children:
            html += f"\n<{tag}>"
            for child in children:
                html += render_list_block(child, notion)
            html += f"</{tag}>"
    return html + "</li>\n"

def convert_embed_url(url: str) -> str:
    """
    埋め込み対象URLを iframe 用のURLに変換
    現在対応: YouTube, ニコニコ動画
    """
    # YouTube対応
    if "youtu.be" in url:
        video_id = url.split("/")[-1]
        return f"https://www.youtube.com/embed/{video_id}"
    elif "youtube.com/watch?v=" in url:
        video_id = url.split("v=")[-1].split("&")[0]
        return f"https://www.youtube.com/embed/{video_id}"

    # ニコニコ動画対応
    if "nicovideo.jp/watch/" in url:
        video_id = url.split("/")[-1]
        return f"https://embed.nicovideo.jp/watch/{video_id}"

    return url

def render_block_html(block, notion):
    import html
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.formatters import HtmlFormatter
    from pygments.util import ClassNotFound

    btype = block["type"]
    data = block.get(btype, {})
    text = render_rich_text(data.get("rich_text", []))

    if btype == "paragraph":
        return f"<p>{text}</p>"
    if btype.startswith("heading_"):
        level = btype[-1]
        return f'<h{level}>{text}</h{level}>'
    if btype == "quote":
        return f"<blockquote>{text}</blockquote>"
    if btype == "code":
        if btype == "code":
            lang = data.get("language", "").strip().lower()
            log(f"💡 コードブロック言語: '{lang}'")
        lang = data.get("language", "").strip().lower()
        code_text = "".join(t.get("plain_text", "") for t in data.get("rich_text", []))
        try:
            lexer = get_lexer_by_name(lang) if lang else TextLexer()
        except ClassNotFound:
            log(f"⚠️ 未対応の言語 '{lang}' → fallback to TextLexer")
            lexer = TextLexer()
        formatter = HtmlFormatter(nowrap=True)
        highlighted = highlight(code_text, lexer, formatter)
        return f'<div class="code-block"><pre>{highlighted}</pre></div>'
    if btype in ["bulleted_list_item", "numbered_list_item"]:
        return render_list_block(block, notion)
    if btype == "to_do":
        checked = "checked" if data.get("checked", False) else ""
        return f'<div class="checkbox"><label><input type="checkbox" disabled {checked}> {text}</label></div>'
    if btype == "table":
        rows = notion.blocks.children.list(block_id=block["id"])['results']
        table_html = "<table>"
        for i, row in enumerate(rows):
            cells = row.get("table_row", {}).get("cells", [])
            tag = "th" if i == 0 else "td"
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

    log(f"⚠️ 未対応のブロックタイプ: {btype}")
    return f"<div>{text}</div>"

# --- Notion データ取得とHTML生成 ---
def notion_pull():
    log("🚀 Notionデータの取得とHTML出力を開始します")

    if OUTPUT_DIR.exists():
        log(f"📁 既存の出力先 {OUTPUT_DIR} を削除します")
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir()
    (OUTPUT_DIR / "posts").mkdir(parents=True, exist_ok=True)

    # staticファイル処理
    theme_static = Path(f"themes/{THEME}/static")
    output_static = OUTPUT_DIR / "static"
    if theme_static.exists():
        log("📂 テーマ static ファイルをコピー中...")
        for item in theme_static.rglob("*"):
            rel_path = item.relative_to(theme_static)
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

    if Path("src").exists():
        log("📦 src ディレクトリをコピーします")
        shutil.copytree("src", OUTPUT_DIR / "src")

    # テンプレート準備
    env = Environment(loader=FileSystemLoader(f"themes/{THEME}/templates"))
    tpl_post = env.get_template("post.html")
    tpl_index = env.get_template("index.html")

    try:
        response = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={"property": "公開", "checkbox": {"equals": True}}
        )
    except (APIResponseError, requests.exceptions.RequestException) as e:
        log(f"❌ Notion APIエラー: {e}")
        raise

    posts_info = []
    for page in response["results"]:
        props = page["properties"]
        title_data = props.get("記事", {}).get("title", [])
        title = title_data[0]["plain_text"] if title_data else "無題"
        slug = to_slug(title)
        page_id = page["id"]

        log(f"📝 記事: {title} (slug: {slug})")
        blocks = notion.blocks.children.list(block_id=page_id)["results"]

        html_blocks, current_list_tag = [], None
        for block in blocks:
            btype = block["type"]
            if btype in ["bulleted_list_item", "numbered_list_item"]:
                tag = "ul" if btype == "bulleted_list_item" else "ol"
                if current_list_tag != tag:
                    if current_list_tag:
                        html_blocks.append(f"</{current_list_tag}>")
                    html_blocks.append(f"<{tag}>")
                    current_list_tag = tag
                html_blocks.append(render_list_block(block, notion))
            else:
                if current_list_tag:
                    html_blocks.append(f"</{current_list_tag}>")
                    current_list_tag = None
                html_blocks.append(render_block_html(block, notion))
        if current_list_tag:
            html_blocks.append(f"</{current_list_tag}>")

        content_html = "\n".join(html_blocks)
        post_dir = OUTPUT_DIR / "posts" / slug
        safe_mkdir(post_dir)
        with open(post_dir / "index.html", "w", encoding="utf-8") as f:
            f.write(tpl_post.render(title=title, content=content_html, **render_context))
        posts_info.append({"title": title, "url": f"posts/{slug}/index.html"})

    with open(OUTPUT_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(tpl_index.render(posts=posts_info, **render_context))
    log("✅ サイト生成完了")


# --- サーバー ---
def serve():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = parser.parse_args()

    os.chdir(OUTPUT_DIR)
    server = HTTPServer(("localhost", args.port), SimpleHTTPRequestHandler)
    url = f"http://localhost:{args.port}"
    print(f"🌐 サーバー起動: {url}")
    print("⛔ Ctrl+Cで停止")

    if not args.no_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("🛑 サーバーを停止しました。")


# --- 実行 ---
if __name__ == "__main__":
    try:
        notion_pull()
    except (APIResponseError, requests.exceptions.RequestException):
        exit(1)
    serve()
