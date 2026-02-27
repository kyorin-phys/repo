import streamlit as st
import requests
from rapidfuzz import fuzz
import xml.etree.ElementTree as ET

st.set_page_config(page_title="文献実在性チェッカー (日英対応)", layout="wide")

st.title("🛡️ 参考文献・実在性チェッカー")
st.markdown("学生のレポートにある参考文献を1行ずつチェックし、架空の文献（ハルシネーション）を炙り出します。")

# --- 検索ロジック関数 ---

def search_crossref(query):
    """海外論文DB (Crossref) で検索"""
    try:
        url = "https://api.crossref.org/works"
        res = requests.get(url, params={"query": query, "rows": 1}, timeout=5).json()
        items = res.get("message", {}).get("items", [])
        if items:
            title = items[0].get("title", [""])[0]
            doi = items[0].get("DOI", "")
            return title, f"https://doi.org/{doi}", "Crossref (海外)"
    except:
        pass
    return None, None, None

def search_cinii(query):
    """日本論文DB (CiNii Research) で検索"""
    try:
        # RSS(OpenSearch)を利用した簡易検索
        url = "https://ci.nii.ac.jp/opensearch/author"
        res = requests.get(url, params={"q": query, "count": 1}, timeout=5)
        root = ET.fromstring(res.text)
        # XMLからタイトルと言語、リンクを抽出
        ns = {'atom': 'http://www.w3.org/2005/Atom', 'dc': 'http://purl.org/dc/elements/1.1/'}
        entry = root.find('atom:entry', ns)
        if entry is not None:
            title = entry.find('atom:title', ns).text
            link = entry.find('atom:link', ns).attrib['href']
            return title, link, "CiNii (国内)"
    except:
        pass
    return None, None, None

# --- UI部分 ---

with st.sidebar:
    st.header("判定の見方")
    st.success("✅ **実在確実**: タイトルがほぼ一致。")
    st.warning("⚠️ **要確認**: 似た論文はあるがタイトルが異なる。")
    st.error("🚨 **捏造の疑い**: どのDBにも存在しません。")

input_text = st.text_area("参考文献リストを貼り付けてください", height=200)

if st.button("一括チェック実行"):
    if not input_text.strip():
        st.info("テキストを入力してください。")
    else:
        lines = [l.strip() for l in input_text.split('\n') if l.strip()]
        
        for line in lines:
            with st.spinner(f"検索中: {line[:30]}..."):
                # 1. Crossrefで検索
                found_title, link, source = search_crossref(line)
                
                # 2. 見つからない、または類似度が低い場合はCiNiiで検索
                if not found_title or fuzz.token_sort_ratio(line.lower(), found_title.lower()) < 60:
                    cinii_title, cinii_link, cinii_source = search_cinii(line)
                    if cinii_title:
                        found_title, link, source = cinii_title, cinii_link, cinii_source
                
                # 判定と表示
                if found_title:
                    score = fuzz.token_sort_ratio(line.lower(), found_title.lower())
                    
                    if score > 85:
                        st.success(f"✅ **実在確実** (スコア:{score:.0f} / 出典:{source})")
                        st.write(f"DB上のタイトル: **{found_title}**")
                        st.caption(f"[リンクを開く]({link})")
                    elif score > 40:
                        st.warning(f"⚠️ **要確認** (スコア:{score:.0f} / 出典:{source})")
                        st.write(f"入力: {line}")
                        st.write(f"DB上の近似論文: **{found_title}**")
                        st.caption(f"[この論文が元ネタか確認する]({link})")
                    else:
                        st.error(f"🚨 **捏造の疑い大** (スコア:{score:.0f})")
                        st.write(f"入力: {line}")
                        st.markdown(f"[Google Scholarで最終確認](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")
                else:
                    st.error(f"🚨 **捏造の疑い大** (DBにヒットなし)")
                    st.write(f"入力: {line}")
                    st.markdown(f"[Google Scholarで最終確認](https://scholar.google.com/scholar?q={line.replace(' ', '+')})")
            
            st.divider()