import streamlit as st
import requests

st.set_page_config(page_title="文献チェッカー", layout="wide")

def check_existence(query):
    try:
        # 検索。上位5件まで見る（本物が少し下に隠れている場合があるため）
        res = requests.get("https://api.crossref.org/works", 
                           params={"query": query, "rows": 5}, timeout=5).json()
        items = res.get("message", {}).get("items", [])
        
        for item in items:
            found_title = item.get("title", [""])[0]
            if not found_title: continue
            
            # 【ここが修正の肝】
            # DBのタイトルが「入力された行」の中に、単語として含まれているかをチェック
            # タイトルそのものが含まれていれば、著者名や年号がどうであれ「実在」とみなす
            if found_title.lower() in query.lower() or query.lower() in found_title.lower():
                return {"status": "PASS", "title": found_title, "link": f"https://doi.org/{item.get('DOI', '')}"}
        
        # どのタイトルも含まれていなければ、最も近いものを1つだけ参考に出す
        if items:
            return {"status": "FAIL", "title": items[0].get("title", [""])[0]}
        return None
    except:
        return None

st.title("🛡️ 文献実在チェッカー (高精度版)")

input_text = st.text_area("参考文献リストを貼り付けてください", height=200)

if st.button("チェック実行"):
    lines = [l.strip() for l in input_text.split('\n') if l.strip()]
    
    for line in lines:
        result = check_existence(line)
        
        # タイトルがしっかり含まれていれば、先生には見せない
        if result and result["status"] == "PASS":
            with st.expander(f"✅ 実在確認済み: {result['title'][:60]}...", expanded=False):
                st.write(f"入力: {line}")
                st.write(f"DB一致: **{result['title']}**")
        else:
            # 含まれていなければ、赤枠で警告。これだけを先生が見れば良い。
            st.error(f"🚨 捏造の疑い / 要確認")
            col1, col2 = st.columns(2)
            with col1:
                st.write("**学生の入力:**")
                st.write(line)
            with col2:
                if result:
                    st.write("**DB上の近似文献（タイトル不一致）:**")
                    st.write(result['title'])
                else:
                    st.write("該当なし")
            st.markdown(f"🔍 [Google Scholarで最終確認](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")
        st.divider()