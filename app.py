import time
import re
import requests
import streamlit as st

st.set_page_config(page_title="文献チェッカー", layout="wide")

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
DEFAULT_PASS_THRESHOLD = 55
DEFAULT_WARN_THRESHOLD = 35


# ─────────────────────────────────────────────
# 参考文献行のパーサー
# ─────────────────────────────────────────────
def parse_reference(line: str) -> dict:
    """
    参考文献行から年・DOIだけを抽出する。
    タイトル推定は行わない（書き方が不定なため信頼できない）。
    スコアリングは「入力行全体 vs DBの各フィールド」で行う。
    """
    info = {"raw": line, "years": [], "doi": None}

    # 年（1900〜2099）を全て抽出（グループなしで4桁まとめてマッチ）
    info["years"] = re.findall(r'(?:19|20)\d{2}', line)

    # DOI
    doi_match = re.search(r'10\.\d{4,}/\S+', line)
    if doi_match:
        info["doi"] = doi_match.group(0).rstrip('.')

    return info


# ─────────────────────────────────────────────
# CrossRef API ラッパー
# ─────────────────────────────────────────────
CROSSREF_SELECT = "DOI,title,author,published,container-title,score"


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



def search_bibliographic(query: str, rows: int = 8) -> list:
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
# スコアリング
# ─────────────────────────────────────────────
def score_item(item: dict, parsed: dict) -> dict:
    """
    CrossRefの1件について入力情報との一致度を0〜100点でスコアリングする。

    タイトル推定に頼らず「DBタイトルの語が入力行に何割含まれるか」で判定するため、
    参考文献の書き方（順序・フォーマット）に依存しない。

    内訳:
      タイトル語カバレッジ … 最大60点
        DBタイトルの意味語（3文字以上・ストップワード除外）が
        入力行に何割含まれているかで線形配点
      年の一致             … 最大20点
      著者姓の一致         … 最大20点（最初の4著者を対象）
    """
    db_title = " ".join(item.get("title", [""])).strip()
    raw_lower = parsed["raw"].lower()
    score = 0
    details = []

    # ① タイトル語カバレッジ（最大60点）
    title_score = 0
    if db_title:
        stopwords = {
            'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are', 'was',
            'not', 'but', 'its', 'via', 'using', 'based', 'new', 'study', 'into'
        }
        db_words = set(
            w for w in re.findall(r'[a-zA-Z]{3,}', db_title.lower())
            if w not in stopwords
        )
        if db_words:
            matched_words = sum(1 for w in db_words if w in raw_lower)
            coverage = matched_words / len(db_words)
            title_score = int(coverage * 60)
            details.append(
                f"タイトル語カバレッジ: {matched_words}/{len(db_words)}語 "
                f"({coverage*100:.0f}%) → {title_score}点"
            )
    score += title_score

    # ② 年の一致（最大20点）
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

    # ③ 著者姓の一致（最大20点）
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

    return {
        "score": score,
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

    # DOIが含まれていれば最優先で直接確認
    if parsed.get("doi"):
        item = lookup_doi(parsed["doi"])
        if item:
            result = score_item(item, parsed)
            result["score"] = max(result["score"], 80)  # DOI直接確認は高信頼
            result["status"] = "PASS"
            result["method"] = "DOI直接確認"
            return result

    # 入力行全体でbibliographic検索（書き方に依存しない）
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
            "status": "UNKNOWN",
            "title": "",
            "score": 0,
            "doi": "",
            "year": "",
            "journal": "",
            "authors": "",
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
    st.progress(min(score, 100))

    with st.expander("スコア内訳を見る"):
        for d in result.get("details", []):
            st.write(f"- {d}")

    st.markdown(
        f"[🔍 Google Scholarで最終確認](https://scholar.google.com/scholar?q={requests.utils.quote(line)})"
    )


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────
st.title("🛡️ 文献実在チェッカー")
st.caption(
    "CrossRef API を用いて参考文献の実在を多角的に検証します。"
    "タイトル類似度・著者名・発表年の3軸でスコアリングします。"
)

with st.sidebar:
    st.header("⚙️ 判定しきい値の設定")
    pass_th = st.slider("✅ PASSしきい値（点）", 30, 80, DEFAULT_PASS_THRESHOLD)
    warn_th = st.slider("⚠️ WARNしきい値（点）", 10, pass_th - 5, DEFAULT_WARN_THRESHOLD)
    st.markdown("---")
    st.markdown("""
**スコア内訳（100点満点）**
| 項目 | 最大 | 方法 |
|------|------|------|
| タイトル語カバレッジ | 60点 | DBタイトルの語が入力行に何割含まれるか |
| 発表年の一致 | 20点 | 入力行に含まれる年とDBの年を照合 |
| 著者名の一致 | 20点 | DB著者姓が入力行に含まれるか |

**判定ランク**
- ✅ **PASS**：しきい値以上（実在の可能性が高い）
- ⚠️ **WARN**：要注意（手動確認を推奨）
- 🚨 **FAIL**：しきい値未満（捏造の疑い）
- ❓ **不明**：DBに候補なし
    """)

input_text = st.text_area(
    "参考文献リストを貼り付けてください（1行1文献）",
    height=240,
    placeholder=(
        "例:\n"
        "Yamada T, et al. (2021) Effects of exercise on cognition. J Physiol, 10(2), 100-110.\n"
        "Smith J. (2019) Deep learning review. Nature, 550, 324-335. doi:10.1038/nature24270"
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
            label = f"✅ [{result['score']}点] {result['title'][:70] or line[:70]}"
            with st.expander(label, expanded=False):
                render_detail(line, result, pass_th, warn_th)

        elif status == "WARN":
            warn_count += 1
            label = f"⚠️ [{result['score']}点] 要注意: {line[:65]}"
            with st.expander(label, expanded=True):
                st.warning("一部の情報がDBと一致しない可能性があります。手動確認を推奨します。")
                render_detail(line, result, pass_th, warn_th)

        elif status == "FAIL":
            fail_count += 1
            label = f"🚨 [{result['score']}点] 捏造の疑い: {line[:60]}"
            with st.expander(label, expanded=True):
                st.error("タイトル・著者・年がDBと大きく乖離しています。")
                render_detail(line, result, pass_th, warn_th)

        else:  # UNKNOWN
            unknown_count += 1
            label = f"❓ 検索不能: {line[:65]}"
            with st.expander(label, expanded=False):
                st.warning("CrossRefで候補が見つかりませんでした。")
                st.code(line, language=None)
                st.markdown(
                    f"[🔍 Google Scholarで手動確認]"
                    f"(https://scholar.google.com/scholar?q={requests.utils.quote(line)})"
                )

        time.sleep(0.2)  # API レート制限対策

    progress.empty()

    # ─── サマリー ───
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
