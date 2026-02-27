import time
import re
import requests
import streamlit as st

st.set_page_config(page_title="文献チェッカー", layout="wide")

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
DEFAULT_PASS_THRESHOLD = 60
DEFAULT_WARN_THRESHOLD = 40

STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are', 'was',
    'not', 'but', 'its', 'via', 'using', 'based', 'new', 'study', 'into',
    'some', 'has', 'have', 'been', 'their', 'they', 'were', 'will', 'also',
    'when', 'than', 'more', 'such', 'each', 'over', 'both', 'after', 'under',
}

CROSSREF_SELECT = "DOI,title,author,published,container-title,score"


# ─────────────────────────────────────────────
# 入力パーサー
# ─────────────────────────────────────────────
def parse_reference(line: str) -> dict:
    """年・DOI・タイトルキーワードを抽出する。書き方の順序には依存しない。"""
    info = {"raw": line, "years": [], "doi": None, "title_keywords": []}

    # 年（グループなし4桁マッチ）
    info["years"] = re.findall(r'(?:19|20)\d{2}', line)

    # DOI
    doi_match = re.search(r'10\.\d{4,}/\S+', line)
    if doi_match:
        info["doi"] = doi_match.group(0).rstrip('.')

    # タイトルキーワード抽出:
    # 年・巻号・ページ・DOI を除去してから4文字以上の英単語を取る
    cleaned = re.sub(r'(?:19|20)\d{2}', ' ', line)
    cleaned = re.sub(r'\b\d+\s*[\(\[]\d+[\)\]]', ' ', cleaned)   # 10(2)
    cleaned = re.sub(r'\b\d{1,4}\s*[-–]\s*\d{1,4}\b', ' ', cleaned)  # 100-200
    cleaned = re.sub(r'10\.\d{4,}/\S+', ' ', cleaned)             # DOI
    words = re.findall(r'[a-zA-Z]{4,}', cleaned.lower())
    info["title_keywords"] = [w for w in words if w not in STOPWORDS]

    return info


# ─────────────────────────────────────────────
# CrossRef API ラッパー
# ─────────────────────────────────────────────
def _fetch_crossref(params: dict) -> list:
    try:
        res = requests.get(
            "https://api.crossref.org/works",
            params={**params, "select": CROSSREF_SELECT},
            timeout=8,
        )
        res.raise_for_status()
        return res.json().get("message", {}).get("items", [])
    except Exception:
        return []


def search_bibliographic(query: str, rows: int = 10) -> list:
    return _fetch_crossref({"query.bibliographic": query, "rows": rows})


def lookup_doi(doi: str):
    try:
        res = requests.get(f"https://api.crossref.org/works/{doi}", timeout=6)
        if res.status_code == 200:
            return res.json().get("message", {})
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# スコアリング（双方向カバレッジ）
# ─────────────────────────────────────────────
def score_item(item: dict, parsed: dict) -> dict:
    """
    タイトル判定を「双方向カバレッジの調和平均（F1的）」で行う。

    従来の「DBタイトルの語が入力に含まれるか」だけだと、
    捏造タイトルでもDBが近い論文を返すためスルーされてしまう。

    双方向にすることで：
      入力→DB方向: 入力のキーワードがDBタイトルに存在するか
      DB→入力方向: DBタイトルの語が入力行に存在するか
    両方が高くないとスコアが上がらないため、捏造を検出しやすくなる。

    内訳（最大110点、表示は100点換算）:
      タイトル双方向F1    … 最大60点
      CrossRef内部スコア  … 最大10点ボーナス（完全一致ほど高い）
      年の一致            … 最大20点
      著者姓の一致        … 最大20点
    """
    db_title = " ".join(item.get("title", [""])).strip()
    raw_lower = parsed["raw"].lower()
    db_lower = db_title.lower()
    score = 0
    details = []

    # ① タイトル双方向F1（最大60点）
    title_score = 0
    input_kw = parsed.get("title_keywords", [])
    db_words = set(
        w for w in re.findall(r'[a-zA-Z]{3,}', db_lower)
        if w not in STOPWORDS
    )

    if input_kw and db_words:
        # A: 入力KW → DBタイトル方向（入力の語がDBにあるか）
        cov_a = sum(1 for w in input_kw if w in db_lower) / len(input_kw)
        # B: DBタイトル → 入力方向（DBの語が入力にあるか）
        cov_b = sum(1 for w in db_words if w in raw_lower) / len(db_words)
        # 調和平均（F1）
        if cov_a + cov_b > 0:
            f1 = 2 * cov_a * cov_b / (cov_a + cov_b)
        else:
            f1 = 0.0
        title_score = int(f1 * 60)
        details.append(
            f"タイトルF1: 入力→DB {cov_a:.0%} × DB→入力 {cov_b:.0%} "
            f"= F1 {f1:.0%} → {title_score}点"
        )
    score += title_score

    # ② CrossRef内部スコアボーナス（最大10点）
    # CrossRefのscoreは完全一致で数百、部分一致で数十、無関係で一桁
    cf_score = item.get("score", 0)
    cf_bonus = int(min(cf_score / 200.0, 1.0) * 10)
    score += cf_bonus
    details.append(f"CrossRef関連度スコア: {cf_score:.1f} → ボーナス {cf_bonus}点")

    # ③ 年の一致（最大20点）
    year_score = 0
    date_parts = (item.get("published", {}).get("date-parts") or [[]])[0]
    db_year = str(date_parts[0]) if date_parts else ""
    input_years = parsed.get("years", [])
    if input_years and db_year:
        if db_year in input_years:
            year_score = 20
            details.append(f"年一致 ({db_year}) → 20点")
        else:
            details.append(f"年不一致 (入力:{input_years} / DB:{db_year}) → 0点")
    score += year_score

    # ④ 著者姓の一致（最大20点）
    author_score = 0
    authors = item.get("author", [])
    if authors:
        matched = sum(
            1 for a in authors[:4]
            if len(a.get("family", "")) >= 2
            and a.get("family", "").lower() in raw_lower
        )
        if matched >= 2:
            author_score = 20
        elif matched == 1:
            author_score = 10
        details.append(f"著者一致: {matched}名 → {author_score}点")
    score += author_score

    # 110点満点を100点換算
    score_100 = min(int(score / 110 * 100), 100)

    return {
        "score": score_100,
        "score_raw": score,
        "title": db_title,
        "doi": item.get("DOI", ""),
        "year": db_year,
        "journal": " ".join(item.get("container-title", [])),
        "authors": ", ".join(a.get("family", "") for a in item.get("author", [])[:3]),
        "details": details,
    }


# ─────────────────────────────────────────────
# メイン判定ロジック
# ─────────────────────────────────────────────
def check_reference(line: str, pass_th: int, warn_th: int) -> dict:
    parsed = parse_reference(line)

    # DOI直接確認（最優先・最高信頼）
    if parsed.get("doi"):
        item = lookup_doi(parsed["doi"])
        if item:
            result = score_item(item, parsed)
            result["score"] = max(result["score"], 85)
            result["status"] = "PASS"
            result["method"] = "DOI直接確認"
            return result

    candidates = search_bibliographic(line)

    # 重複DOI除去
    seen: set = set()
    unique = []
    for c in candidates:
        doi = c.get("DOI", "")
        if doi not in seen:
            seen.add(doi)
            unique.append(c)

    if not unique:
        return {
            "status": "UNKNOWN", "title": "", "score": 0,
            "doi": "", "year": "", "journal": "", "authors": "",
            "details": ["CrossRef APIから候補が見つかりませんでした"],
            "method": "検索失敗",
        }

    scored = [score_item(c, parsed) for c in unique]
    best = max(scored, key=lambda x: x["score"])

    if best["score"] >= pass_th:
        best["status"] = "PASS"
    elif best["score"] >= warn_th:
        best["status"] = "WARN"
    else:
        best["status"] = "FAIL"

    best["method"] = "CrossRef検索"
    return best


# ─────────────────────────────────────────────
# UI ヘルパー
# ─────────────────────────────────────────────
def render_detail(line: str, result: dict, pass_th: int, warn_th: int):
    col1, col2 = st.columns(2)
    with col1:
        st.write("**入力された文献:**")
        st.code(line, language=None)
    with col2:
        st.write("**DB上の最近似文献:**")
        st.write(f"📄 {result.get('title') or '不明'}")
        st.write(f"👤 {result.get('authors') or '不明'}　📅 {result.get('year') or '不明'}")
        st.write(f"📰 {result.get('journal') or '不明'}")
        if result.get("doi"):
            st.markdown(f"🔗 [DOIリンク](https://doi.org/{result['doi']})")

    score = result.get("score", 0)
    color = "green" if score >= pass_th else "orange" if score >= warn_th else "red"
    st.markdown(f"**総合スコア: :{color}[{score} / 100点]**　（確認方法: {result.get('method', '')}）")
    st.progress(score)

    with st.expander("スコア内訳を見る"):
        for d in result.get("details", []):
            st.write(f"- {d}")

    st.markdown(
        f"[🔍 Google Scholarで最終確認]"
        f"(https://scholar.google.com/scholar?q={requests.utils.quote(line)})"
    )


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────
st.title("🛡️ 文献実在チェッカー")
st.caption(
    "CrossRef API を用いて参考文献の実在を検証します。"
    "タイトル双方向F1・CrossRef関連度・著者・年の4軸でスコアリングします。"
)

with st.sidebar:
    st.header("⚙️ 判定しきい値の設定")
    pass_th = st.slider("✅ PASSしきい値（点）", 30, 80, DEFAULT_PASS_THRESHOLD)
    warn_th = st.slider("⚠️ WARNしきい値（点）", 10, pass_th - 5, DEFAULT_WARN_THRESHOLD)
    st.markdown("---")
    st.markdown("""
**スコア内訳（100点換算）**
| 項目 | 最大 | 内容 |
|------|------|------|
| タイトルF1 | 60点 | 入力↔DBの双方向カバレッジ調和平均 |
| CrossRef関連度 | 10点 | APIの内部一致スコア |
| 発表年 | 20点 | 年の一致 |
| 著者名 | 20点 | 著者姓の一致 |

**判定ランク**
- ✅ **PASS**：実在の可能性が高い
- ⚠️ **WARN**：要注意・手動確認推奨
- 🚨 **FAIL**：捏造の疑い
- ❓ **不明**：DBに候補なし

**ポイント**
タイトルF1は「DBの語が入力にある」だけでなく
「入力の語がDBにある」も同時に確認するため、
タイトルを微妙に改変した捏造を検出できます。
    """)

input_text = st.text_area(
    "参考文献リストを貼り付けてください（1行1文献）",
    height=240,
    placeholder=(
        "例:\n"
        "Vaswani et al. 2017 Attention is all you need NeurIPS\n"
        "attention is all you need vaswani 2017\n"
        "Smith J. (2019) Deep learning review. Nature, 550, 324-335."
    ),
)

if st.button("✅ チェック実行", type="primary"):
    lines = [line.strip() for line in input_text.split("\n") if line.strip()]
    if not lines:
        st.warning("文献が入力されていません。")
        st.stop()

    pass_count = warn_count = fail_count = unknown_count = 0
    progress = st.progress(0, text="チェック中...")

    for i, line in enumerate(lines):
        result = check_reference(line, pass_th, warn_th)
        progress.progress((i + 1) / len(lines), text=f"チェック中... ({i + 1}/{len(lines)})")
        status = result.get("status", "UNKNOWN")

        if status == "PASS":
            pass_count += 1
            with st.expander(
                f"✅ [{result['score']}点] {result['title'][:70] or line[:70]}",
                expanded=False
            ):
                render_detail(line, result, pass_th, warn_th)

        elif status == "WARN":
            warn_count += 1
            with st.expander(
                f"⚠️ [{result['score']}点] 要注意: {line[:65]}",
                expanded=True
            ):
                st.warning("一部の情報がDBと一致しない可能性があります。手動確認を推奨します。")
                render_detail(line, result, pass_th, warn_th)

        elif status == "FAIL":
            fail_count += 1
            with st.expander(
                f"🚨 [{result['score']}点] 捏造の疑い: {line[:60]}",
                expanded=True
            ):
                st.error("タイトル・著者・年がDBと大きく乖離しています。")
                render_detail(line, result, pass_th, warn_th)

        else:
            unknown_count += 1
            with st.expander(f"❓ 検索不能: {line[:65]}", expanded=False):
                st.warning("CrossRefで候補が見つかりませんでした。")
                st.code(line, language=None)
                st.markdown(
                    f"[🔍 Google Scholarで手動確認]"
                    f"(https://scholar.google.com/scholar?q={requests.utils.quote(line)})"
                )

        time.sleep(0.2)

    progress.empty()

    st.markdown("---")
    st.subheader("📊 チェック結果サマリー")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ PASS", pass_count)
    c2.metric("⚠️ WARN", warn_count)
    c3.metric("🚨 FAIL", fail_count)
    c4.metric("❓ 不明", unknown_count)

    total = len(lines)
    fail_rate = (fail_count + warn_count) / total * 100 if total else 0
    if fail_rate > 30:
        st.error(f"⚠️ 全体の {fail_rate:.0f}% が要確認です。精査を強く推奨します。")
    elif fail_rate > 0:
        st.warning(f"全体の {fail_rate:.0f}% に要確認の文献があります。")
    else:
        st.success("すべての文献が実在確認できました。")
