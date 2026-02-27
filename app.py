import streamlit as st
import requests
from rapidfuzz import fuzz
import xml.etree.ElementTree as ET
import re

st.set_page_config(page_title="文献検閲ツール", layout="wide")

# --- ロジック：ノイズ除去 ---
def clean_query(text):
    """著者名や年号、行番号などのノイズを削り、タイトル周辺を抽出する"""
    # 1. 先頭の数字や記号（1. など）を削除
    text = re.sub(r'^\d+[\.\s\-、]+', '', text)
    # 2. (2020) などの年号以降をタイトル候補として抽出
    match = re.search(r'\(\d{4}\)\.?\s*(.*)', text)
    if match:
        return match.group(1).strip()
    return text.strip()

# --- ロジック：DB検索（上位5件から最良のものを探す） ---
def search_bibliographies(query):
    query_core = clean_query(query)
    results = []

    # 1. Crossref検索
    try:
        res = requests.get("https://api.crossref.org/works", 
                           params={"query": query_core, "rows": 5}, timeout=5).json()
        for item in res.get("message", {}).get("items", []):
            title = item.get("title", [""])[0]
            link = f"https://doi.org/{item.get('DOI', '')}"
            score = fuzz.token_sort_ratio(query.lower(), title.lower())
            results.append({"title": title, "link": link, "score": score, "source": "Crossref"})
    except: pass

    # 2. CiNii検索
    try:
        res = requests.get("https://ci.nii.ac.jp/opensearch/author", 
                           params={"q": query_core, "count": 3}, timeout=5)
        root = ET.fromstring(res.text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('atom:entry', ns):
            title = entry.find('atom:title', ns).text
            link = entry.find('atom:link', ns).attrib['href']
            score = fuzz.token_sort_ratio(query.lower(), title.lower())
            results.append({"title": title, "link": link, "score": score, "source": "CiNii"})
    except: pass

    if not results:
        return None
    # スコアが最も高いものを返す
    return max(results, key=lambda x: x['score'])

# --- UI：メイン画面 ---
st.title("🛡️ 文献実在チェッカー (改良版)")
st.markdown("先生の手間を最小化するため、**「実在が怪しいもの」**を優先的に表示します。")

input_text = st.text_area("参考文献リストを貼り付けてください（1行1文献）", height=200)

if st.button("チェック実行"):
    if not input_text.strip():
        st.info("テキストを入力してください。")
    else:
        lines = [l.strip() for l in input_text.split('\n') if l.strip()]
        
        # 結果を格納するリスト
        verified = []
        suspicious = []

        for line in lines:
            with st.spinner(f"検証中... {line[:20]}"):
                best_match = search_bibliographies(line)
                
                if best_match and best_match['score'] >= 85:
                    verified.append((line, best_match))
                else:
                    suspicious.append((line, best_match))

        # --- 表示フェーズ ---
        
        # 1. 疑わしい文献（ここを重点的に見る）
        st.subheader(f"🚨 要確認・捏造の疑い ({len(suspicious)}件)")
        if not suspicious:
            st.success("疑わしい文献は見つかりませんでした！")
        for line, match in suspicious:
            with st.expander(f"⚠️ {line[:60]}...", expanded=True):
                col1, col2 = st.columns([1, 1])
                with col1:
                    st.error("**学生の入力:**")
                    st.write(line)
                with col2:
                    if match:
                        st.warning(f"**DB上の最も近い文献** (一致度: {match['score']:.0f}%)")
                        st.write(match['title'])
                        st.caption(f"[出典: {match['source']} / リンク]({match['link']})")
                    else:
                        st.error("**該当なし**")
                        st.write("学術データベースに類似する文献が一切見つかりません。")
                st.markdown(f"🔍 [Google Scholarで検索](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")

        st.divider()

        # 2. 実在が確認された文献（基本スルーでOK）
        with st.expander(f"✅ 実在確認済み ({len(verified)}件)"):
            for line, match in verified:
                st.write(f"🟢 **{match['title']}**")
                st.caption(f"入力: {line}")