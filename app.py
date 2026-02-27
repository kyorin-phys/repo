import streamlit as st
import requests
from rapidfuzz import fuzz
import xml.etree.ElementTree as ET

st.set_page_config(page_title="文献実在チェッカー", layout="wide")

# --- 検索ロジック：最初のコードの強さを継承 ---
def search_bibliographies(query):
    results = []
    # 1. Crossref検索 (上位3件取得して最良を判定)
    try:
        res = requests.get("https://api.crossref.org/works", 
                           params={"query": query, "rows": 3}, timeout=5).json()
        for item in res.get("message", {}).get("items", []):
            title = item.get("title", [""])[0]
            link = f"https://doi.org/{item.get('DOI', '')}"
            # 入力文全体とDBタイトルの類似度を計算
            score = fuzz.token_sort_ratio(query.lower(), title.lower())
            results.append({"title": title, "link": link, "score": score, "source": "Crossref"})
    except: pass

    # 2. CiNii検索
    try:
        res = requests.get("https://ci.nii.ac.jp/opensearch/author", 
                           params={"q": query, "count": 3}, timeout=5)
        root = ET.fromstring(res.text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('atom:entry', ns):
            title = entry.find('atom:title', ns).text
            link = entry.find('atom:link', ns).attrib['href']
            score = fuzz.token_sort_ratio(query.lower(), title.lower())
            results.append({"title": title, "link": link, "score": score, "source": "CiNii"})
    except: pass

    if not results: return None
    return max(results, key=lambda x: x['score'])

# --- UI ---
st.title("🛡️ 文献実在チェッカー")
st.markdown("貼り付けられた文献をDBと照合し、**「実在が確認できたもの」**と**「怪しいもの」**に自動で仕分けます。")

input_text = st.text_area("参考文献リスト（1行1文献）", height=200, placeholder="ここにコピペしてください")

if st.button("チェック実行"):
    if not input_text.strip():
        st.info("テキストを入力してください。")
    else:
        lines = [l.strip() for l in input_text.split('\n') if l.strip()]
        
        verified = []
        suspicious = []

        for line in lines:
            with st.spinner(f"検証中..."):
                match = search_bibliographies(line)
                # スコア判定（最初のコードでうまくいっていた基準を採用）
                if match and match['score'] > 75: 
                    verified.append((line, match))
                else:
                    suspicious.append((line, match))

        # --- 表示：先生がチェックすべき「怪しいもの」を最優先 ---
        st.subheader(f"🚩 要確認 ({len(suspicious)}件)")
        if not suspicious:
            st.success("全ての文献の実在が確認されました。")
        else:
            for line, match in suspicious:
                with st.container():
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        st.markdown("**⚠️ 学生の入力:**")
                        st.error(line)
                    with col2:
                        if match:
                            st.markdown(f"**🔍 DB上の候補** (一致度: {match['score']:.0f}%)")
                            st.warning(match['title'])
                            st.caption(f"[出典: {match['source']} / リンク]({match['link']})")
                        else:
                            st.markdown("**❌ 該当なし**")
                            st.error("学術データベースに類似する文献がありません。")
                    st.markdown(f"🔗 [Google Scholarで最終確認](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")
                    st.divider()

        # --- 表示：実在確実（デフォルトで折りたたむ） ---
        if verified:
            with st.expander(f"✅ 実在確認済み ({len(verified)}件) -- ここはチェック不要です"):
                for line, match in verified:
                    st.write(f"🟢 **{match['title']}**")
                    st.caption(f"入力: {line}")