import time
import re
import requests
import streamlit as st
from rapidfuzz import fuzz

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
    典型的な参考文献フォーマットから年・タイトル候補・DOIを抽出する。

    対応フォーマット例:
      Vaswani A, et al. (2017) Attention is all you need. Advances in NeurIPS, 30.
      Smith J. Title of paper. J Phys. 2018;5:123. doi:10.1234/abc
      山田太郎. (2020). タイトル. 雑誌名, 10(2), 100-110.
    """
    info = {"raw": line, "year": None, "title_guess": None, "doi": None}

    # 年（1900〜2099）
    year_match = re.search(r'\b(19|20)\d{2}\b', line)
    if year_match:
        info["year"] = year_match.group(0)

    # DOI
    doi_match = re.search(r'10\.\d{4,}/\S+', line)
    if doi_match:
        info["doi"] = doi_match.group(0).rstrip('.')

    # ── タイトル推定（優先度順） ──────────────────────────────────────

    # 戦略1: "(年)" または "年." の直後のセグメントをタイトルとみなす
    # 例: "Vaswani A, et al. (2017) Attention is all you need. Advances..."
    #      → "(2017) の直後" = "Attention is all you need"
    title_after_year = None
    m = re.search(r'[\(\s](19|20)\d{2}[\)\.\s]\s*(.+?)(?:\.\s|\.$|$)', line)
    if m:
        candidate = m.group(2).strip()
        # 雑誌名・巻号っぽい特徴がなければ採用
        if len(candidate) > 10 and not _looks_like_journal_info(candidate):
            title_after_year = candidate

    # 戦略2: ピリオド区切りで「最もタイトルらしいセグメント」を選ぶ
    segments = [s.strip() for s in re.split(r'\.\s+', line) if s.strip()]
    title_candidates = []
    for s in segments:
        if len(s) < 10:
            continue
        if _looks_like_author_segment(s):
            continue
        if _looks_like_journal_info(s):
            continue
        # 年を含むセグメントは年の後ろ部分だけ取り出す
        s_clean = re.sub(r'^.*?(19|20)\d{2}[)\s.]+', '', s).strip()
        if len(s_clean) > 10:
            title_candidates.append(s_clean)
        elif len(s) > 10:
            title_candidates.append(s)

    # タイトルらしさスコア：小文字が多い・長い・数字が少ない
    def title_score(s: str) -> float:
        alpha = sum(c.isalpha() for c in s)
        digit = sum(c.isdigit() for c in s)
        return alpha - digit * 3 + len(s) * 0.1

    best_segment = max(title_candidates, key=title_score) if title_candidates else None

    # 戦略1 > 戦略2 の優先順で採用
    info["title_guess"] = title_after_year or best_segment
    return info


def _looks_like_journal_info(s: str) -> bool:
    """巻号・ページ・雑誌略称っぽい特徴を持つか判定する"""
    # 巻号パターン: "10(2)", "30:123-145", "vol.10"
    if re.search(r'\d+\s*[\(\[]\s*\d+\s*[\)\]]', s):
        return True
    if re.search(r'\b\d+\s*:\s*\d+', s):
        return True
    if re.search(r'\bvol\.?\s*\d+\b', s, re.IGNORECASE):
        return True
    # ページ番号: "123-456"
    if re.search(r'\b\d{1,4}\s*[-–]\s*\d{1,4}\b', s):
        return True
    # 大文字略称だらけ（雑誌略称）: "J Phys Chem" など
    words = s.split()
    if len(words) <= 5 and sum(1 for w in words if w[0:1].isupper()) == len(words):
        # 全単語が大文字始まりかつ短い → 雑誌名の可能性
        if all(len(w) <= 10 for w in words):
            return True
    return False


def _looks_like_author_segment(s: str) -> bool:
    """著者リストっぽい特徴を持つか判定する"""
    # "et al" を含む
    if re.search(r'\bet\s+al\b', s, re.IGNORECASE):
        return True
    # "A, B, C" 形式でカンマ区切りの短い単語の羅列
    parts = [p.strip() for p in s.split(',')]
    if len(parts) >= 2 and all(len(p) < 25 for p in parts):
        # 各パートにイニシャルっぽい大文字1文字を含む
        if sum(1 for p in parts if re.search(r'\b[A-Z]\b', p)) >= 2:
            return True
    return False


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


def search_by_title(title: str, rows: int = 5) -> list:
    return _fetch_crossref({"query.title": title, "rows": rows})


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

    内訳:
      タイトル類似度（token_sort_ratio） … 最大60点
      年の一致                           … 最大20点
      著者名（最初の3著者）の一致        … 最大20点
    """
    db_title = " ".join(item.get("title", [""])).strip()
    score = 0
    details = []

    # ① タイトル類似度（最大60点）
    title_score = 0
    if parsed.get("title_guess") and db_title:
        ratio = fuzz.token_sort_ratio(
            parsed["title_guess"].lower(), db_title.lower()
        )
        title_score = int(ratio * 0.60)
        details.append(f"タイトル類似度: {ratio:.0f}% → {title_score}点")
    score += title_score

    # ② 年の一致（最大20点）
    year_score = 0
    date_parts = (item.get("published", {}).get("date-parts") or [[]])[0]
    db_year = str(date_parts[0]) if date_parts else ""
    if parsed.get("year"):
        if db_year == parsed["year"]:
            year_score = 20
            details.append(f"年一致 ({db_year}) → 20点")
        else:
            details.append(f"年不一致 (入力:{parsed['year']} / DB:{db_year}) → 0点")
    score += year_score

    # ③ 著者名の一致（最大20点）
    author_score = 0
    authors = item.get("author", [])
    if authors:
        raw_lower = parsed["raw"].lower()
        matched = sum(
            1 for a in authors[:3]
            if a.get("family", "").lower() and a.get("family", "").lower() in raw_lower
        )
        if matched >= 2:
            author_score = 20
        elif matched == 1:
            author_score = 10
        details.append(f"著者一致: {matched}/3名 → {author_score}点")
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

    # タイトル推定クエリ ＋ 全文クエリの2段階検索
    candidates = []
    if parsed.get("title_guess"):
        candidates += search_by_title(parsed["title_guess"])
    candidates += search_bibliographic(line)

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
| 項目 | 最大 |
|------|------|
| タイトル類似度 | 60点 |
| 発表年の一致 | 20点 |
| 著者名の一致 | 20点 |

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
