import streamlit as st
import requests
from rapidfuzz import fuzz

st.set_page_config(page_title="文献チェッカー", layout="wide")

def get_best_match(query):
    try:
        # Crossrefで検索
        res = requests.get("https://api.crossref.org/works", 
                           params={"query": query, "rows": 3}, timeout=5).json()
        items = res.get("message", {}).get("items", [])
        
        best_score = 0
        best_match = None

        for item in items:
            found_title = item.get("title", [""])[0]
            # 【重要】query(入力)の中に、found_title(DBのタイトル)が含まれているかを判定
            # これにより「著者名が含まれていてもタイトルが合っていれば高得点」になる
            score = fuzz.partial_ratio(found_title.lower(), query.lower())
            
            if score > best_score:
                best_score = score
                best_match = {"title": found_title, "score": score, "link": f"https://doi.org/{item.get('DOI', '')}"}
        
        return best_match
    except:
        return None

st.title("🛡️ 文献実在チェッカー（最終改善版）")

input_text = st.text_area("参考文献リストを貼り付けてください", height=200)

if st.button("実行"):
    lines = [l.strip() for l in input_text.split('\n') if l.strip()]
    
    for line in lines:
        match = get_best_match(line)
        
        # スコア判定のしきい値を調整（部分一致なら85以上はほぼ本物）
        if match and match['score'] >= 85:
            with st.expander(f"✅ 実在: {match['title'][:50]}...", expanded=False):
                st.write(f"入力: {line}")
                st.write(f"DB一致: **{match['title']}**")
        else:
            # 怪しいものだけを目立たせる
            st.error(f"🚨 捏造の疑い / 要確認")
            col1, col2 = st.columns(2)
            with col1:
                st.write("**学生の入力:**")
                st.write(line)
            with col2:
                if match:
                    st.write(f"**DB上の最も近い文献** (一致率: {match['score']:.0f}%)")
                    st.write(match['title'])
                else:
                    st.write("該当なし")
            st.markdown(f"🔍 [Google Scholar確認](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")
        st.divider()