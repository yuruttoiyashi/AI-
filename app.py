import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
from io import StringIO
from openai import OpenAI

import pandas as pd
import plotly.express as px
import streamlit as st


# =========================================================
# 基本設定
# =========================================================
st.set_page_config(
    page_title="物流向けAI在庫管理アプリ",
    page_icon="📦",
    layout="wide"
)

APP_TITLE = "物流向けAI在庫管理アプリ"
DB_DIR = Path("data")
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "inventory.db"


# =========================================================
# APIキー取得
# =========================================================
def get_api_key() -> str:
    """Streamlit secrets → 環境変数 の順で取得"""
    try:
        api_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        api_key = ""

    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")

    return api_key


# =========================================================
# DB関連
# =========================================================
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT UNIQUE NOT NULL,
            product_name TEXT NOT NULL,
            category TEXT,
            unit TEXT,
            location TEXT,
            min_stock REAL DEFAULT 0,
            optimal_stock REAL DEFAULT 0,
            supplier TEXT,
            remarks TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            product_code TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            partner TEXT,
            staff TEXT,
            remarks TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_code) REFERENCES products(product_code)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# 共通ユーティリティ
# =========================================================
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def safe_contains(series: pd.Series, keyword: str) -> pd.Series:
    return series.astype(str).str.contains(keyword, case=False, na=False)


def format_number(value) -> str:
    try:
        if float(value).is_integer():
            return str(int(float(value)))
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


def normalize_date_string(value) -> str:
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        raise ValueError("日付の形式が不正です。YYYY-MM-DD 形式推奨です。")


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名や値に混ざった不要なダブルクォーテーションや空白を除去する
    """
    df = df.copy()

    df.columns = [
        str(col).replace("\ufeff", "").replace('"', "").replace("'", "").strip()
        for col in df.columns
    ]

    for col in df.columns:
        df[col] = df[col].apply(
            lambda x: str(x).replace("\ufeff", "").replace('"', "").replace("'", "").strip()
            if pd.notna(x) else x
        )

    return df


def read_flexible_csv(uploaded_file) -> pd.DataFrame:
    """
    強めのCSV読み込み
    - utf-8-sig / utf-8 / cp932 / shift_jis 対応
    - , / ; / タブ / 全角カンマ 対応
    - 1列で読まれた場合は手動splitで救済
    """
    raw = uploaded_file.getvalue()

    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    text = None

    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue

    if text is None:
        raise ValueError("CSVの文字コードを読み取れませんでした。utf-8 または cp932 で保存してください。")

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    candidates = [",", ";", "\t", "，"]
    best_df = None
    best_cols = 0

    for sep in candidates:
        try:
            df = pd.read_csv(StringIO(text), sep=sep)
            if df.shape[1] > best_cols:
                best_df = df
                best_cols = df.shape[1]
        except Exception:
            pass

    if best_df is not None and best_cols > 1:
        return clean_dataframe(best_df)

    # 1列で読まれた場合の救済
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        raise ValueError("CSVが空です。")

    sep = None
    for candidate in [",", ";", "\t", "，"]:
        if candidate in lines[0]:
            sep = candidate
            break

    if sep is None:
        raise ValueError("CSVの区切り文字を判定できませんでした。半角カンマ区切りで保存してください。")

    split_rows = [line.split(sep) for line in lines]
    max_len = max(len(row) for row in split_rows)
    split_rows = [row + [""] * (max_len - len(row)) for row in split_rows]

    header = [col.strip() for col in split_rows[0]]
    data_rows = [[cell.strip() for cell in row] for row in split_rows[1:]]

    df = pd.DataFrame(data_rows, columns=header)
    return clean_dataframe(df)


# =========================================================
# テンプレートCSV
# =========================================================
def get_product_template_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "商品コード": "P001",
            "商品名": "段ボール箱 100サイズ",
            "カテゴリ": "資材",
            "単位": "箱",
            "保管場所": "A-01",
            "最低在庫数": 50,
            "適正在庫数": 200,
            "仕入先": "山田梱包資材",
            "備考": "出荷用標準箱"
        },
        {
            "商品コード": "P002",
            "商品名": "緩衝材 エアパッキン",
            "カテゴリ": "資材",
            "単位": "巻",
            "保管場所": "A-02",
            "最低在庫数": 10,
            "適正在庫数": 40,
            "仕入先": "東日本包装",
            "備考": "壊れ物梱包用"
        }
    ])


def get_inbound_template_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "取引日": "2026-03-30",
            "商品コード": "P001",
            "数量": 100,
            "取引先": "山田梱包資材",
            "担当者": "佐藤",
            "備考": "初回入庫"
        },
        {
            "取引日": "2026-03-30",
            "商品コード": "P002",
            "数量": 20,
            "取引先": "東日本包装",
            "担当者": "佐藤",
            "備考": "追加補充"
        }
    ])


def get_outbound_template_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "取引日": "2026-03-30",
            "商品コード": "P001",
            "数量": 15,
            "取引先": "ABC商事",
            "担当者": "田中",
            "備考": "出荷分"
        },
        {
            "取引日": "2026-03-30",
            "商品コード": "P002",
            "数量": 5,
            "取引先": "XYZ物流",
            "担当者": "田中",
            "備考": "緊急出庫"
        }
    ])


# =========================================================
# 商品マスタ関連
# =========================================================
def add_product(
    product_code: str,
    product_name: str,
    category: str,
    unit: str,
    location: str,
    min_stock: float,
    optimal_stock: float,
    supplier: str,
    remarks: str
):
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO products (
                product_code, product_name, category, unit, location,
                min_stock, optimal_stock, supplier, remarks, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product_code.strip(),
            product_name.strip(),
            category.strip(),
            unit.strip(),
            location.strip(),
            float(min_stock),
            float(optimal_stock),
            supplier.strip(),
            remarks.strip(),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return True, "商品を登録しました。"
    except sqlite3.IntegrityError:
        return False, "この商品コードは既に登録されています。"
    except Exception as e:
        return False, f"登録中にエラーが発生しました: {e}"
    finally:
        conn.close()


def import_products_from_csv(df_csv: pd.DataFrame):
    required_cols = [
        "商品コード", "商品名", "カテゴリ", "単位", "保管場所",
        "最低在庫数", "適正在庫数", "仕入先", "備考"
    ]

    missing_cols = [col for col in required_cols if col not in df_csv.columns]
    if missing_cols:
        return False, f"CSVに必要な列がありません: {', '.join(missing_cols)}", []

    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    errors = []

    try:
        for idx, row in df_csv.iterrows():
            try:
                product_code = str(row["商品コード"]).strip()
                product_name = str(row["商品名"]).strip()

                if not product_code or product_code.lower() == "nan":
                    errors.append(f"{idx + 2}行目: 商品コードが空です")
                    continue

                if not product_name or product_name.lower() == "nan":
                    errors.append(f"{idx + 2}行目: 商品名が空です")
                    continue

                category = str(row["カテゴリ"]).strip() if pd.notna(row["カテゴリ"]) else ""
                unit = str(row["単位"]).strip() if pd.notna(row["単位"]) else ""
                location = str(row["保管場所"]).strip() if pd.notna(row["保管場所"]) else ""
                min_stock = float(row["最低在庫数"]) if pd.notna(row["最低在庫数"]) else 0
                optimal_stock = float(row["適正在庫数"]) if pd.notna(row["適正在庫数"]) else 0
                supplier = str(row["仕入先"]).strip() if pd.notna(row["仕入先"]) else ""
                remarks = str(row["備考"]).strip() if pd.notna(row["備考"]) else ""

                if optimal_stock < min_stock:
                    errors.append(f"{idx + 2}行目: 適正在庫数が最低在庫数より小さいです")
                    continue

                cur.execute("""
                    INSERT INTO products (
                        product_code, product_name, category, unit, location,
                        min_stock, optimal_stock, supplier, remarks, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    product_code,
                    product_name,
                    category,
                    unit,
                    location,
                    min_stock,
                    optimal_stock,
                    supplier,
                    remarks,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                inserted += 1

            except sqlite3.IntegrityError:
                skipped += 1
            except Exception as e:
                errors.append(f"{idx + 2}行目: {e}")

        conn.commit()
        message = f"取込完了: {inserted}件登録 / {skipped}件スキップ"
        if errors:
            message += f" / エラー {len(errors)}件"
        return True, message, errors

    except Exception as e:
        conn.rollback()
        return False, f"CSV取込中にエラーが発生しました: {e}", []
    finally:
        conn.close()


def get_products() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM products ORDER BY product_code", conn)
    conn.close()
    return df


def get_product_options() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT product_code, product_name FROM products ORDER BY product_code",
        conn
    )
    conn.close()
    return df


def get_product_code_set() -> set:
    products = get_product_options()
    if products.empty:
        return set()
    return set(products["product_code"].astype(str).tolist())


# =========================================================
# 入出庫関連
# =========================================================
def add_transaction(
    transaction_date: str,
    product_code: str,
    transaction_type: str,
    quantity: float,
    partner: str,
    staff: str,
    remarks: str
):
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO stock_transactions (
                transaction_date, product_code, transaction_type,
                quantity, partner, staff, remarks, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            transaction_date,
            product_code,
            transaction_type,
            float(quantity),
            partner.strip(),
            staff.strip(),
            remarks.strip(),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return True, "登録しました。"
    except Exception as e:
        return False, f"登録中にエラーが発生しました: {e}"
    finally:
        conn.close()


def import_inbound_from_csv(df_csv: pd.DataFrame):
    required_cols = ["取引日", "商品コード", "数量", "取引先", "担当者", "備考"]
    missing_cols = [col for col in required_cols if col not in df_csv.columns]
    if missing_cols:
        return False, f"CSVに必要な列がありません: {', '.join(missing_cols)}", []

    valid_codes = get_product_code_set()
    if not valid_codes:
        return False, "先に商品マスタを登録してください。", []

    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    errors = []

    try:
        for idx, row in df_csv.iterrows():
            try:
                transaction_date = normalize_date_string(row["取引日"])
                product_code = str(row["商品コード"]).strip()
                quantity = float(row["数量"]) if pd.notna(row["数量"]) else 0
                partner = str(row["取引先"]).strip() if pd.notna(row["取引先"]) else ""
                staff = str(row["担当者"]).strip() if pd.notna(row["担当者"]) else ""
                remarks = str(row["備考"]).strip() if pd.notna(row["備考"]) else ""

                if not product_code or product_code.lower() == "nan":
                    errors.append(f"{idx + 2}行目: 商品コードが空です")
                    continue

                if product_code not in valid_codes:
                    errors.append(f"{idx + 2}行目: 商品コード {product_code} は商品マスタに存在しません")
                    continue

                if quantity <= 0:
                    errors.append(f"{idx + 2}行目: 数量は0より大きくしてください")
                    continue

                cur.execute("""
                    INSERT INTO stock_transactions (
                        transaction_date, product_code, transaction_type,
                        quantity, partner, staff, remarks, created_at
                    )
                    VALUES (?, ?, 'IN', ?, ?, ?, ?, ?)
                """, (
                    transaction_date,
                    product_code,
                    quantity,
                    partner,
                    staff,
                    remarks,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                inserted += 1

            except Exception as e:
                errors.append(f"{idx + 2}行目: {e}")

        conn.commit()
        message = f"入庫CSV取込完了: {inserted}件登録"
        if errors:
            message += f" / エラー {len(errors)}件"
        return True, message, errors

    except Exception as e:
        conn.rollback()
        return False, f"入庫CSV取込中にエラーが発生しました: {e}", []
    finally:
        conn.close()


def get_current_stock_map() -> dict:
    df_inventory = get_inventory_data()
    if df_inventory.empty:
        return {}
    return {
        str(row["product_code"]): float(row["current_stock"])
        for _, row in df_inventory.iterrows()
    }


def import_outbound_from_csv(df_csv: pd.DataFrame):
    required_cols = ["取引日", "商品コード", "数量", "取引先", "担当者", "備考"]
    missing_cols = [col for col in required_cols if col not in df_csv.columns]
    if missing_cols:
        return False, f"CSVに必要な列がありません: {', '.join(missing_cols)}", []

    valid_codes = get_product_code_set()
    if not valid_codes:
        return False, "先に商品マスタを登録してください。", []

    stock_map = get_current_stock_map()

    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    errors = []

    try:
        for idx, row in df_csv.iterrows():
            try:
                transaction_date = normalize_date_string(row["取引日"])
                product_code = str(row["商品コード"]).strip()
                quantity = float(row["数量"]) if pd.notna(row["数量"]) else 0
                partner = str(row["取引先"]).strip() if pd.notna(row["取引先"]) else ""
                staff = str(row["担当者"]).strip() if pd.notna(row["担当者"]) else ""
                remarks = str(row["備考"]).strip() if pd.notna(row["備考"]) else ""

                if not product_code or product_code.lower() == "nan":
                    errors.append(f"{idx + 2}行目: 商品コードが空です")
                    continue

                if product_code not in valid_codes:
                    errors.append(f"{idx + 2}行目: 商品コード {product_code} は商品マスタに存在しません")
                    continue

                if quantity <= 0:
                    errors.append(f"{idx + 2}行目: 数量は0より大きくしてください")
                    continue

                current_stock = float(stock_map.get(product_code, 0))
                if quantity > current_stock:
                    errors.append(
                        f"{idx + 2}行目: 商品コード {product_code} の在庫不足です（現在庫 {format_number(current_stock)} / 出庫数 {format_number(quantity)}）"
                    )
                    continue

                cur.execute("""
                    INSERT INTO stock_transactions (
                        transaction_date, product_code, transaction_type,
                        quantity, partner, staff, remarks, created_at
                    )
                    VALUES (?, ?, 'OUT', ?, ?, ?, ?, ?)
                """, (
                    transaction_date,
                    product_code,
                    quantity,
                    partner,
                    staff,
                    remarks,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                inserted += 1

                stock_map[product_code] = current_stock - quantity

            except Exception as e:
                errors.append(f"{idx + 2}行目: {e}")

        conn.commit()
        message = f"出庫CSV取込完了: {inserted}件登録"
        if errors:
            message += f" / エラー {len(errors)}件"
        return True, message, errors

    except Exception as e:
        conn.rollback()
        return False, f"出庫CSV取込中にエラーが発生しました: {e}", []
    finally:
        conn.close()


def get_recent_transactions(limit: int = 10) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT
            transaction_date,
            product_code,
            transaction_type,
            quantity,
            partner,
            staff,
            remarks
        FROM stock_transactions
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        conn,
        params=(limit,)
    )
    conn.close()
    return df


def get_today_transaction_counts():
    today_str = date.today().strftime("%Y-%m-%d")
    conn = get_connection()

    in_df = pd.read_sql(
        """
        SELECT COUNT(*) AS cnt
        FROM stock_transactions
        WHERE transaction_date = ? AND transaction_type = 'IN'
        """,
        conn,
        params=(today_str,)
    )

    out_df = pd.read_sql(
        """
        SELECT COUNT(*) AS cnt
        FROM stock_transactions
        WHERE transaction_date = ? AND transaction_type = 'OUT'
        """,
        conn,
        params=(today_str,)
    )

    conn.close()
    return int(in_df.iloc[0]["cnt"]), int(out_df.iloc[0]["cnt"])


# =========================================================
# 在庫計算
# =========================================================
def get_inventory_data() -> pd.DataFrame:
    conn = get_connection()

    df_products = pd.read_sql("SELECT * FROM products", conn)

    if df_products.empty:
        conn.close()
        return pd.DataFrame()

    df_in = pd.read_sql("""
        SELECT product_code, SUM(quantity) AS total_in
        FROM stock_transactions
        WHERE transaction_type = 'IN'
        GROUP BY product_code
    """, conn)

    df_out = pd.read_sql("""
        SELECT product_code, SUM(quantity) AS total_out
        FROM stock_transactions
        WHERE transaction_type = 'OUT'
        GROUP BY product_code
    """, conn)

    conn.close()

    df = df_products.merge(df_in, on="product_code", how="left")
    df = df.merge(df_out, on="product_code", how="left")

    df["total_in"] = df["total_in"].fillna(0.0)
    df["total_out"] = df["total_out"].fillna(0.0)
    df["current_stock"] = df["total_in"] - df["total_out"]

    def judge_status(row):
        current_stock = float(row.get("current_stock", 0))
        min_stock = float(row.get("min_stock", 0))
        optimal_stock = float(row.get("optimal_stock", 0))

        if current_stock <= min_stock:
            return "要発注"
        elif current_stock <= min_stock + 5:
            return "注意"
        elif optimal_stock > 0 and current_stock > optimal_stock * 1.5:
            return "過剰在庫"
        else:
            return "正常"

    df["status"] = df.apply(judge_status, axis=1)

    status_order = {"要発注": 0, "注意": 1, "正常": 2, "過剰在庫": 3}
    df["status_order"] = df["status"].map(status_order)
    df = df.sort_values(["status_order", "product_code"]).drop(columns=["status_order"])

    return df


def get_low_stock_items(df_inventory: pd.DataFrame) -> pd.DataFrame:
    if df_inventory.empty:
        return pd.DataFrame()
    return df_inventory[df_inventory["status"] == "要発注"].copy()


# =========================================================
# AI分析
# =========================================================
def generate_ai_advice(inventory_df: pd.DataFrame, low_stock_df: pd.DataFrame) -> str:
    api_key = get_api_key()

    # APIキーがない場合は簡易分析
    if not api_key:
        if inventory_df.empty:
            return "### 🤖 AI分析\n\nまだ分析できる在庫データがありません。"

        total_items = len(inventory_df)
        low_count = len(low_stock_df)
        over_stock_df = inventory_df[inventory_df["status"] == "過剰在庫"]

        advice_lines = []
        advice_lines.append("### 🤖 AI在庫分析レポート（簡易モード）")
        advice_lines.append("")
        advice_lines.append(f"- 登録商品数: **{total_items}件**")
        advice_lines.append(f"- 要発注商品数: **{low_count}件**")
        advice_lines.append(f"- 過剰在庫候補: **{len(over_stock_df)}件**")
        advice_lines.append("")

        if low_count > 0:
            advice_lines.append("#### ⚠️ 要発注候補")
            for _, row in low_stock_df.iterrows():
                advice_lines.append(
                    f"- **{row['product_name']}**：現在庫 {format_number(row['current_stock'])} / 最低在庫 {format_number(row['min_stock'])}"
                )
            advice_lines.append("")
            advice_lines.append("**提案:** 早めの補充や発注スケジュール確認をおすすめします。")
        else:
            advice_lines.append("#### ✅ 在庫状況")
            advice_lines.append("現在、最低在庫を下回っている商品はありません。")

        if len(over_stock_df) > 0:
            advice_lines.append("")
            advice_lines.append("#### 📦 過剰在庫候補")
            for _, row in over_stock_df.iterrows():
                advice_lines.append(
                    f"- **{row['product_name']}**：現在庫 {format_number(row['current_stock'])} / 適正在庫 {format_number(row['optimal_stock'])}"
                )
            advice_lines.append("")
            advice_lines.append("**提案:** 入庫調整や出庫促進を検討してください。")

        return "\n".join(advice_lines)

    # データが空なら終了
    if inventory_df.empty:
        return "### 🤖 AI分析\n\nまだ分析できる在庫データがありません。"

    # OpenAI用に送るデータを絞る
    inventory_summary_df = inventory_df[
        ["product_code", "product_name", "current_stock", "min_stock", "optimal_stock", "status"]
    ].copy()

    inventory_summary = inventory_summary_df.to_csv(index=False)

    # プロンプト作成
    prompt = f"""
あなたは物流会社の在庫管理アドバイザーです。
以下の在庫データを見て、日本語でわかりやすく分析してください。

条件:
- 要発注の商品を優先して伝える
- 過剰在庫の可能性がある商品も伝える
- 現場担当者にも分かるやさしい表現で書く
- 箇条書きで簡潔にまとめる
- 最後に「今日やるべきこと」を2〜3個提案する
- Markdown形式で見出し付きにする

在庫データ:
{inventory_summary}
"""

    try:
        client = OpenAI(api_key=api_key)

        response = client.responses.create(
            model="gpt-5.4",
            input=prompt
        )

        # SDKの戻り値から本文を安全に取り出す
        output_text = getattr(response, "output_text", None)

        if output_text and str(output_text).strip():
            return output_text

        # 念のためのフォールバック
        return "### 🤖 AI分析\n\nAIからの応答はありましたが、本文を取得できませんでした。"

    except Exception as e:
        return f"""### 🤖 AI分析エラー

OpenAI APIの呼び出し中にエラーが発生しました。

**エラー内容:**
`{e}`

現在は簡易分析モードでの利用をおすすめします。
"""

# =========================================================
# 表示用
# =========================================================
def show_header():
    st.title(f"📦 {APP_TITLE}")
    st.caption("物流会社・倉庫業務向けのMVP在庫管理システム")


def show_dashboard():
    st.subheader("📊 ダッシュボード")

    df_inventory = get_inventory_data()

    if df_inventory.empty:
        total_products = 0
        total_stock = 0
        reorder_items = 0
    else:
        total_products = len(df_inventory)
        total_stock = df_inventory["current_stock"].sum()
        reorder_items = len(df_inventory[df_inventory["status"] == "要発注"])

    today_in, today_out = get_today_transaction_counts()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("登録商品数", f"{total_products} 点")
    col2.metric("総在庫数", f"{format_number(total_stock)} 単位")
    col3.metric("要発注商品", f"{reorder_items} 件")
    col4.metric("本日入庫", f"{today_in} 件")
    col5.metric("本日出庫", f"{today_out} 件")

    st.markdown("### ⚠️ 要発注商品一覧")
    if df_inventory.empty:
        st.info("まだ商品データがありません。")
    else:
        low_stock_df = get_low_stock_items(df_inventory)
        if low_stock_df.empty:
            st.success("現在、要発注商品はありません。")
        else:
            st.warning(f"現在 {len(low_stock_df)} 件の商品が最低在庫を下回っています。")
            display_cols = ["product_code", "product_name", "current_stock", "min_stock", "location", "supplier"]
            st.dataframe(low_stock_df[display_cols], use_container_width=True)

    st.markdown("### 📝 直近の入出庫履歴")
    recent_logs = get_recent_transactions(10)
    if recent_logs.empty:
        st.info("まだ入出庫履歴はありません。")
    else:
        st.dataframe(recent_logs, use_container_width=True)

    if not df_inventory.empty:
        st.markdown("### 📈 商品別在庫数")
        graph_df = df_inventory[["product_name", "current_stock", "status"]].copy()
        fig = px.bar(
            graph_df,
            x="product_name",
            y="current_stock",
            color="status",
            title="商品別在庫状況",
            color_discrete_map={
                "正常": "green",
                "注意": "orange",
                "要発注": "red",
                "過剰在庫": "blue"
            }
        )
        st.plotly_chart(fig, use_container_width=True)


def show_product_form():
    st.subheader("🆕 商品マスタ登録")

    with st.form("product_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            product_code = st.text_input("商品コード *", help="必須・重複不可")
            product_name = st.text_input("商品名 *", help="必須")
            category = st.selectbox("カテゴリ", ["食品", "日用品", "電化製品", "資材", "その他"])
            unit = st.text_input("単位", value="pcs")

        with col2:
            location = st.text_input("保管場所")
            min_stock = st.number_input("最低在庫数", min_value=0.0, value=10.0, step=1.0)
            optimal_stock = st.number_input("適正在庫数", min_value=0.0, value=50.0, step=1.0)
            supplier = st.text_input("仕入先")

        remarks = st.text_area("備考")
        submitted = st.form_submit_button("登録する")

    if submitted:
        if not product_code.strip() or not product_name.strip():
            st.error("商品コードと商品名は必須です。")
            return

        if optimal_stock < min_stock:
            st.error("適正在庫数は最低在庫数以上にしてください。")
            return

        success, message = add_product(
            product_code=product_code,
            product_name=product_name,
            category=category,
            unit=unit,
            location=location,
            min_stock=min_stock,
            optimal_stock=optimal_stock,
            supplier=supplier,
            remarks=remarks
        )

        if success:
            st.success(message)
        else:
            st.error(message)


def show_product_csv_import():
    st.subheader("📂 商品マスタCSV取込")

    st.markdown("### ① テンプレートをダウンロード")
    template_df = get_product_template_df()
    st.download_button(
        "商品マスタCSVテンプレートをダウンロード",
        data=to_csv_bytes(template_df),
        file_name="product_master_template.csv",
        mime="text/csv"
    )

    st.markdown("### ② CSVをアップロード")
    uploaded_file = st.file_uploader(
        "商品マスタCSVを選択してください",
        type=["csv"],
        key="product_csv"
    )

    if uploaded_file is not None:
        try:
            df_csv = read_flexible_csv(uploaded_file)
        except Exception as e:
            st.error(f"CSV読み込みエラー: {e}")
            return

        st.markdown("### ③ 読み込み内容プレビュー")
        st.dataframe(df_csv, use_container_width=True)

        if st.button("この商品マスタCSVを取り込む"):
            success, message, errors = import_products_from_csv(df_csv)

            if success:
                st.success(message)
                if errors:
                    st.warning("一部エラーがあります。詳細を確認してください。")
                    for err in errors:
                        st.write(f"- {err}")
            else:
                st.error(message)


def show_product_list():
    st.subheader("📋 商品一覧")

    df = get_products()

    if df.empty:
        st.info("まだ商品が登録されていません。")
        return

    keyword = st.text_input("商品名または商品コードで検索")
    if keyword:
        df = df[
            safe_contains(df["product_name"], keyword) |
            safe_contains(df["product_code"], keyword)
        ]

    display_cols = [
        "product_code", "product_name", "category", "unit",
        "location", "min_stock", "optimal_stock", "supplier", "remarks"
    ]
    st.dataframe(df[display_cols], use_container_width=True)

    st.download_button(
        "CSVをダウンロード",
        data=to_csv_bytes(df[display_cols]),
        file_name="products.csv",
        mime="text/csv"
    )


def show_inbound_form():
    st.subheader("📥 入庫登録")

    products = get_product_options()

    if products.empty:
        st.warning("先に商品マスタを登録してください。")
        return

    product_map = {
        f"{row['product_code']} : {row['product_name']}": row["product_code"]
        for _, row in products.iterrows()
    }

    with st.form("inbound_form", clear_on_submit=True):
        transaction_date = st.date_input("入庫日", value=date.today())
        selected_product = st.selectbox("商品を選択", list(product_map.keys()))
        product_code = product_map[selected_product]
        quantity = st.number_input("数量", min_value=1.0, value=1.0, step=1.0)
        partner = st.text_input("入庫元（仕入先など）")
        staff = st.text_input("担当者")
        remarks = st.text_area("備考")

        submitted = st.form_submit_button("入庫を確定する")

    if submitted:
        success, message = add_transaction(
            transaction_date=str(transaction_date),
            product_code=product_code,
            transaction_type="IN",
            quantity=quantity,
            partner=partner,
            staff=staff,
            remarks=remarks
        )
        if success:
            st.success(f"入庫を登録しました。 商品コード: {product_code} / 数量: {format_number(quantity)}")
        else:
            st.error(message)


def show_inbound_csv_import():
    st.subheader("📥 入庫CSV一括取込")

    st.markdown("### ① テンプレートをダウンロード")
    template_df = get_inbound_template_df()
    st.download_button(
        "入庫CSVテンプレートをダウンロード",
        data=to_csv_bytes(template_df),
        file_name="inbound_template.csv",
        mime="text/csv"
    )

    st.markdown("### ② CSVをアップロード")
    uploaded_file = st.file_uploader(
        "入庫CSVを選択してください",
        type=["csv"],
        key="inbound_csv"
    )

    if uploaded_file is not None:
        try:
            df_csv = read_flexible_csv(uploaded_file)
        except Exception as e:
            st.error(f"CSV読み込みエラー: {e}")
            return

        st.markdown("### ③ 読み込み内容プレビュー")
        st.dataframe(df_csv, use_container_width=True)

        if st.button("この入庫CSVを取り込む"):
            success, message, errors = import_inbound_from_csv(df_csv)

            if success:
                st.success(message)
                if errors:
                    st.warning("一部エラーがあります。詳細を確認してください。")
                    for err in errors:
                        st.write(f"- {err}")
            else:
                st.error(message)


def show_outbound_form():
    st.subheader("📤 出庫登録")

    df_inventory = get_inventory_data()

    if df_inventory.empty:
        st.warning("先に商品マスタを登録してください。")
        return

    product_map = {
        f"{row['product_code']} : {row['product_name']}（現在庫: {format_number(row['current_stock'])}）": row["product_code"]
        for _, row in df_inventory.iterrows()
    }

    with st.form("outbound_form", clear_on_submit=True):
        transaction_date = st.date_input("出庫日", value=date.today())
        selected_product = st.selectbox("商品を選択", list(product_map.keys()))
        product_code = product_map[selected_product]

        current_stock = float(
            df_inventory.loc[df_inventory["product_code"] == product_code, "current_stock"].iloc[0]
        )
        st.info(f"現在庫: {format_number(current_stock)}")

        quantity = st.number_input("数量", min_value=1.0, value=1.0, step=1.0)
        partner = st.text_input("出庫先（顧客など）")
        staff = st.text_input("担当者")
        remarks = st.text_area("備考")

        submitted = st.form_submit_button("出庫を確定する")

    if submitted:
        if quantity > current_stock:
            st.error(f"在庫不足です。現在庫は {format_number(current_stock)} です。")
            return

        success, message = add_transaction(
            transaction_date=str(transaction_date),
            product_code=product_code,
            transaction_type="OUT",
            quantity=quantity,
            partner=partner,
            staff=staff,
            remarks=remarks
        )
        if success:
            st.success(f"出庫を登録しました。 商品コード: {product_code} / 数量: {format_number(quantity)}")
        else:
            st.error(message)


def show_outbound_csv_import():
    st.subheader("📤 出庫CSV一括取込")

    st.markdown("### ① テンプレートをダウンロード")
    template_df = get_outbound_template_df()
    st.download_button(
        "出庫CSVテンプレートをダウンロード",
        data=to_csv_bytes(template_df),
        file_name="outbound_template.csv",
        mime="text/csv"
    )

    st.markdown("### ② CSVをアップロード")
    uploaded_file = st.file_uploader(
        "出庫CSVを選択してください",
        type=["csv"],
        key="outbound_csv"
    )

    if uploaded_file is not None:
        try:
            df_csv = read_flexible_csv(uploaded_file)
        except Exception as e:
            st.error(f"CSV読み込みエラー: {e}")
            return

        st.markdown("### ③ 読み込み内容プレビュー")
        st.dataframe(df_csv, use_container_width=True)

        if st.button("この出庫CSVを取り込む"):
            success, message, errors = import_outbound_from_csv(df_csv)

            if success:
                st.success(message)
                if errors:
                    st.warning("一部エラーがあります。詳細を確認してください。")
                    for err in errors:
                        st.write(f"- {err}")
            else:
                st.error(message)


def show_inventory_list():
    st.subheader("📦 在庫一覧")

    df = get_inventory_data()

    if df.empty:
        st.info("まだ在庫データがありません。")
        return

    keyword = st.text_input("商品名または商品コードで検索")
    if keyword:
        df = df[
            safe_contains(df["product_name"], keyword) |
            safe_contains(df["product_code"], keyword)
        ]

    display_df = df[
        ["product_code", "product_name", "category", "location", "current_stock", "min_stock", "optimal_stock", "status"]
    ].copy()

    def highlight_row(row):
        if row["status"] == "要発注":
            return ["background-color: #ffd6d6"] * len(row)
        elif row["status"] == "注意":
            return ["background-color: #fff3cd"] * len(row)
        elif row["status"] == "過剰在庫":
            return ["background-color: #d6ecff"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display_df.style.apply(highlight_row, axis=1),
        use_container_width=True
    )

    st.download_button(
        "在庫データをCSVで保存",
        data=to_csv_bytes(display_df),
        file_name="inventory_status.csv",
        mime="text/csv"
    )


def show_ai_analysis():
    st.subheader("🤖 AI在庫分析アドバイザー")

    df_inventory = get_inventory_data()

    if df_inventory.empty:
        st.info("分析するためのデータがまだありません。")
        return

    low_stock_df = get_low_stock_items(df_inventory)

    with st.spinner("AIがデータを分析中..."):
        advice = generate_ai_advice(df_inventory, low_stock_df)

    st.markdown(advice)

    st.markdown("### 📈 在庫状況グラフ")
    fig = px.bar(
        df_inventory,
        x="product_name",
        y="current_stock",
        color="status",
        title="商品別在庫数とステータス",
        color_discrete_map={
            "正常": "green",
            "注意": "orange",
            "要発注": "red",
            "過剰在庫": "blue"
        }
    )
    st.plotly_chart(fig, use_container_width=True)

    if get_api_key():
        st.success("GEMINI_API_KEY が設定されています。今後API連携を追加できます。")
    else:
        st.warning("GEMINI_API_KEY は未設定です。現在は簡易分析モードで動作しています。")


# =========================================================
# メイン
# =========================================================
def main():
    init_db()
    show_header()

    st.sidebar.title("📦 在庫管理メニュー")
    menu = [
        "ダッシュボード",
        "商品マスタ登録",
        "商品マスタCSV取込",
        "商品一覧",
        "入庫登録",
        "入庫CSV取込",
        "出庫登録",
        "出庫CSV取込",
        "在庫一覧",
        "AI分析"
    ]
    choice = st.sidebar.radio("メニューを選択してください", menu)

    if choice == "ダッシュボード":
        show_dashboard()
    elif choice == "商品マスタ登録":
        show_product_form()
    elif choice == "商品マスタCSV取込":
        show_product_csv_import()
    elif choice == "商品一覧":
        show_product_list()
    elif choice == "入庫登録":
        show_inbound_form()
    elif choice == "入庫CSV取込":
        show_inbound_csv_import()
    elif choice == "出庫登録":
        show_outbound_form()
    elif choice == "出庫CSV取込":
        show_outbound_csv_import()
    elif choice == "在庫一覧":
        show_inventory_list()
    elif choice == "AI分析":
        show_ai_analysis()


if __name__ == "__main__":
    main()
