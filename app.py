import streamlit as st
import requests
from rapidfuzz import fuzz

st.set_page_config(page_title="文献実在チェッカー", page_icon="📚")

st.title("📚 文献実在チェッカー")
st.caption("レポートの参考文献が「架空のデタラメ」ではないか、学術データベース(Crossref)で照合します。")

# 入力エリア
input_text = st.text_area("参考文献リストを貼り付けてください（1行1文献）", height=200, placeholder="例: 山田太郎 (2023). AIと教育の未来. 学術出版.")

if st.button("一括チェック開始"):
    if not input_text.strip():
        st.warning("テキストを入力してください。")
    else:
        refs = [line.strip() for line in input_text.split('\n') if line.strip()]
        
        for ref in refs:
            with st.spinner(f"照合中: {ref[:30]}..."):
                # APIリクエスト (前述のロジック)
                res = requests.get("https://api.crossref.org/works", params={"query": ref, "rows": 1}).json()
                items = res.get("message", {}).get("items", [])
                
                if items:
                    best = items[0]
                    found_title = best.get("title", ["不明"])[0]
                    score = fuzz.partial_ratio(ref.lower(), found_title.lower())
                    
                    # 結果表示の切り替え
                    if score > 80:
                        st.success(f"✅ 実在確実: {found_title}")
                    elif score > 50:
                        st.warning(f"⚠️ 疑わしい (類似度:{score:.0f}%): {found_title} がヒットしましたが、内容が一致しません。")
                    else:
                        st.error(f"❌ 架空の可能性大: データベースに一致する文献が見つかりません。")
                else:
                    st.error(f"❌ 発見不能: {ref}")