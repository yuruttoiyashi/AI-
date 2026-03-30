import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import os
import io

# ==========================================
# 1. データベース初期化
# ==========================================
DB_NAME = "inventory.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 商品マスタテーブル
    c.execute('''
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 入出庫履歴テーブル
    c.execute('''
        CREATE TABLE IF NOT EXISTS stock_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date DATE NOT NULL,
            product_code TEXT NOT NULL,
            transaction_type TEXT NOT NULL, -- IN or OUT
            quantity REAL NOT NULL,
            partner TEXT,
            staff TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# データベース接続用関数
def get_connection():
    return sqlite3.connect(DB_NAME)

# ==========================================
# 2. AI分析ロジック (Gemini API 拡張用)
# ==========================================
def generate_ai_advice(inventory_df, low_stock_df):
    """
    在庫データに基づいたAIアドバイスを生成する関数。
    現在はルールベースだが、将来的にGoogle Gemini API等に置き換え可能。
    """
    # APIキーが設定されているか確認 (将来用)
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key or api_key == "MY_GEMINI_API_KEY":
        # --- ルールベースの簡易コメント (API未設定時) ---
        advice = "### 🤖 AI在庫分析レポート (簡易版)\n\n"
        
        if len(low_stock_df) > 0:
            advice += f"⚠️ **警告:** 現在、{len(low_stock_df)}件の商品が最低在庫を下回っています。早急な発注を検討してください。\n\n"
            for _, row in low_stock_df.iterrows():
                advice += f"- **{row['product_name']}**: 現在庫 {row['current_stock']} (最低在庫: {row['min_stock']})\n"
        else:
            advice += "✅ **良好:** 現在、最低在庫を下回っている商品はありません。\n\n"
            
        # 在庫バランスのチェック
        over_stock = inventory_df[inventory_df['current_stock'] > inventory_df['optimal_stock'] * 1.5]
        if len(over_stock) > 0:
            advice += "\n📦 **過剰在庫の懸念:**\n"
            for _, row in over_stock.iterrows():
                advice += f"- **{row['product_name']}**: 適正在庫の1.5倍を超えています。入庫調整を検討してください。\n"
        
        advice += "\n---\n*※Google Gemini APIを設定すると、より高度な需要予測や最適化アドバイスが受けられます。*"
        return advice
    else:
        # --- ここに Gemini API を使用した高度な分析を実装可能 ---
        # 例: 過去のトレンド分析、季節性の考慮など
        return "🤖 (Gemini API連携モード) 高度な分析結果をここに表示します。"

# ==========================================
# 3. ユーティリティ関数
# ==========================================
def get_inventory_data():
    conn = get_connection()
    # 入庫合計
    in_query = "SELECT product_code, SUM(quantity) as total_in FROM stock_transactions WHERE transaction_type = 'IN' GROUP BY product_code"
    # 出庫合計
    out_query = "SELECT product_code, SUM(quantity) as total_out FROM stock_transactions WHERE transaction_type = 'OUT' GROUP BY product_code"
    
    df_products = pd.read_sql("SELECT * FROM products", conn)
    df_in = pd.read_sql(in_query, conn)
    df_out = pd.read_sql(out_query, conn)
    
    conn.close()
    
    # マージして在庫計算
    df = pd.merge(df_products, df_in, on='product_code', how='left').fillna(0)
    df = pd.merge(df, df_out, on='product_code', how='left').fillna(0)
    df['current_stock'] = df['total_in'] - df['total_out']
    
    # ステータス判定
    def get_status(row):
        if row['current_stock'] <= row['min_stock']:
            return "要発注"
        elif row['current_stock'] <= row['min_stock'] + 5:
            return "注意"
        else:
            return "正常"
            
    df['status'] = df.apply(get_status, axis=1)
    return df

# ==========================================
# 4. メインアプリケーション (Streamlit UI)
# ==========================================
def main():
    st.set_page_config(page_title="AI在庫管理システム", layout="wide")
    init_db()
    
    st.sidebar.title("📦 在庫管理メニュー")
    menu = ["ダッシュボード", "商品マスタ登録", "商品一覧", "入庫登録", "出庫登録", "在庫一覧", "AI分析"]
    choice = st.sidebar.radio("メニューを選択してください", menu)
    
    # ------------------------------------------
    # ダッシュボード
    # ------------------------------------------
    if choice == "ダッシュボード":
        st.header("📊 ダッシュボード")
        df = get_inventory_data()
        
        # 指標の計算
        total_products = len(df)
        total_stock = df['current_stock'].sum()
        reorder_items = len(df[df['status'] == "要発注"])
        
        # 本日の入出庫
        conn = get_connection()
        today = datetime.now().strftime('%Y-%m-%d')
        today_in = pd.read_sql(f"SELECT COUNT(*) as count FROM stock_transactions WHERE transaction_date = '{today}' AND transaction_type = 'IN'", conn).iloc[0]['count']
        today_out = pd.read_sql(f"SELECT COUNT(*) as count FROM stock_transactions WHERE transaction_date = '{today}' AND transaction_type = 'OUT'", conn).iloc[0]['count']
        conn.close()
        
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("登録商品数", f"{total_products} 点")
        col2.metric("総在庫数", f"{int(total_stock)} 単位")
        col3.metric("要発注商品", f"{reorder_items} 件", delta_color="inverse")
        col4.metric("本日入庫", f"{today_in} 件")
        col5.metric("本日出庫", f"{today_out} 件")
        
        st.subheader("⚠️ 要発注商品一覧")
        if reorder_items > 0:
            st.warning(f"現在 {reorder_items} 件の商品が最低在庫を下回っています。")
            st.dataframe(df[df['status'] == "要発注"][['product_code', 'product_name', 'current_stock', 'min_stock', 'location']])
        else:
            st.success("現在、要発注商品はありません。")
            
        # 入出庫サマリー (直近10件)
        st.subheader("📝 直近の入出庫履歴")
        conn = get_connection()
        recent_logs = pd.read_sql("SELECT transaction_date, product_code, transaction_type, quantity, partner, staff FROM stock_transactions ORDER BY created_at DESC LIMIT 10", conn)
        conn.close()
        st.table(recent_logs)

    # ------------------------------------------
    # 商品マスタ登録
    # ------------------------------------------
    elif choice == "商品マスタ登録":
        st.header("🆕 商品マスタ登録")
        with st.form("product_form"):
            col1, col2 = st.columns(2)
            with col1:
                p_code = st.text_input("商品コード *", help="必須項目・重複不可")
                p_name = st.text_input("商品名 *", help="必須項目")
                category = st.selectbox("カテゴリ", ["食品", "日用品", "電化製品", "資材", "その他"])
                unit = st.text_input("単位", value="pcs")
            with col2:
                location = st.text_input("保管場所")
                min_stock = st.number_input("最低在庫数", min_value=0.0, value=10.0)
                optimal_stock = st.number_input("適正在庫数", min_value=0.0, value=50.0)
                supplier = st.text_input("仕入先")
            
            remarks = st.text_area("備考")
            submitted = st.form_submit_button("登録する")
            
            if submitted:
                if not p_code or not p_name:
                    st.error("商品コードと商品名は必須です。")
                else:
                    try:
                        conn = get_connection()
                        c = conn.cursor()
                        c.execute('''
                            INSERT INTO products (product_code, product_name, category, unit, location, min_stock, optimal_stock, supplier, remarks)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (p_code, p_name, category, unit, location, min_stock, optimal_stock, supplier, remarks))
                        conn.commit()
                        conn.close()
                        st.success(f"商品「{p_name}」を登録しました。")
                    except sqlite3.IntegrityError:
                        st.error("この商品コードは既に登録されています。")

    # ------------------------------------------
    # 商品一覧
    # ------------------------------------------
    elif choice == "商品一覧":
        st.header("📋 商品一覧")
        conn = get_connection()
        df = pd.read_sql("SELECT * FROM products", conn)
        conn.close()
        
        search = st.text_input("商品名またはコードで検索")
        if search:
            df = df[df['product_name'].str.contains(search) | df['product_code'].str.contains(search)]
            
        st.dataframe(df)
        
        # CSVダウンロード
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("CSVをダウンロード", csv, "products.csv", "text/csv")

    # ------------------------------------------
    # 入庫登録
    # ------------------------------------------
    elif choice == "入庫登録":
        st.header("📥 入庫登録")
        conn = get_connection()
        products = pd.read_sql("SELECT product_code, product_name FROM products", conn)
        conn.close()
        
        if products.empty:
            st.warning("先に商品マスタを登録してください。")
        else:
            with st.form("in_form"):
                t_date = st.date_input("入庫日", value=datetime.now())
                p_options = products.apply(lambda x: f"{x['product_code']} : {x['product_name']}", axis=1).tolist()
                selected_p = st.selectbox("商品コードを選択", p_options)
                p_code = selected_p.split(" : ")[0]
                
                quantity = st.number_input("数量", min_value=1.0, value=1.0)
                partner = st.text_input("入庫元 (仕入先等)")
                staff = st.text_input("担当者")
                remarks = st.text_area("備考")
                
                submitted = st.form_submit_button("入庫を確定する")
                
                if submitted:
                    conn = get_connection()
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO stock_transactions (transaction_date, product_code, transaction_type, quantity, partner, staff, remarks)
                        VALUES (?, ?, 'IN', ?, ?, ?, ?)
                    ''', (t_date, p_code, quantity, partner, staff, remarks))
                    conn.commit()
                    conn.close()
                    st.success(f"入庫を完了しました。({p_code} : {quantity})")

    # ------------------------------------------
    # 出庫登録
    # ------------------------------------------
    elif choice == "出庫登録":
        st.header("📤 出庫登録")
        df_inv = get_inventory_data()
        
        if df_inv.empty:
            st.warning("先に商品マスタを登録してください。")
        else:
            with st.form("out_form"):
                t_date = st.date_input("出庫日", value=datetime.now())
                p_options = df_inv.apply(lambda x: f"{x['product_code']} : {x['product_name']} (現在庫: {x['current_stock']})", axis=1).tolist()
                selected_p = st.selectbox("商品コードを選択", p_options)
                p_code = selected_p.split(" : ")[0]
                
                # 現在庫の取得
                current_stock = df_inv[df_inv['product_code'] == p_code]['current_stock'].values[0]
                
                quantity = st.number_input("数量", min_value=1.0, value=1.0)
                partner = st.text_input("出庫先 (顧客等)")
                staff = st.text_input("担当者")
                remarks = st.text_area("備考")
                
                submitted = st.form_submit_button("出庫を確定する")
                
                if submitted:
                    if quantity > current_stock:
                        st.error(f"在庫不足です。現在の在庫は {current_stock} です。")
                    else:
                        conn = get_connection()
                        c = conn.cursor()
                        c.execute('''
                            INSERT INTO stock_transactions (transaction_date, product_code, transaction_type, quantity, partner, staff, remarks)
                            VALUES (?, ?, 'OUT', ?, ?, ?, ?)
                        ''', (t_date, p_code, quantity, partner, staff, remarks))
                        conn.commit()
                        conn.close()
                        st.success(f"出庫を完了しました。({p_code} : {quantity})")

    # ------------------------------------------
    # 在庫一覧
    # ------------------------------------------
    elif choice == "在庫一覧":
        st.header("📦 在庫一覧")
        df = get_inventory_data()
        
        search = st.text_input("商品名またはコードで検索")
        if search:
            df = df[df['product_name'].str.contains(search) | df['product_code'].str.contains(search)]
            
        # 表示項目の整理
        display_df = df[['product_code', 'product_name', 'category', 'location', 'current_stock', 'min_stock', 'optimal_stock', 'status']]
        
        # スタイル適用 (要発注を赤くするなど)
        def color_status(val):
            color = 'red' if val == '要発注' else ('orange' if val == '注意' else 'black')
            return f'color: {color}'
            
        st.dataframe(display_df.style.applymap(color_status, subset=['status']))
        
        # CSVダウンロード
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("在庫データをCSVで保存", csv, "inventory_status.csv", "text/csv")

    # ------------------------------------------
    # AI分析
    # ------------------------------------------
    elif choice == "AI分析":
        st.header("🤖 AI在庫分析アドバイザー")
        df = get_inventory_data()
        low_stock_df = df[df['status'] == "要発注"]
        
        if df.empty:
            st.info("分析するためのデータがまだありません。")
        else:
            with st.spinner("AIがデータを分析中..."):
                advice = generate_ai_advice(df, low_stock_df)
                st.markdown(advice)
            
            # 視覚化
            st.subheader("📈 在庫状況グラフ")
            import plotly.express as px
            fig = px.bar(df, x='product_name', y='current_stock', color='status', 
                         title="商品別在庫数とステータス",
                         color_discrete_map={'正常': 'green', '注意': 'orange', '要発注': 'red'})
            st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
