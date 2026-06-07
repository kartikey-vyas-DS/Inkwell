"""
Inkwell — Query Engine v7
===================================
New in this version:
  - web_search(): Brave Search API integration
  - should_web_search(): confidence-based web-search trigger
  - retrieve(): extraction of retrieval pipeline (used by both ask and ask_stream)
  - ask_stream(): generator that yields SSE events for streaming UI
  - ask(): unchanged public interface for non-streaming calls
"""

import os, json, urllib.request, urllib.parse
from pathlib import Path
from typing import Optional, Generator

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import chromadb
import anthropic
import voyageai

import config as cfg

PROJECT   = cfg.load()
BRAIN_DIR = PROJECT["brain_dir"]

PROJECT_CONTEXT = f"""Project: {PROJECT['project_name']}
Description: {PROJECT.get('project_description', '')}
Goals: {'; '.join(PROJECT.get('project_goals', []))}"""

EXPAND_MODEL = "claude-haiku-4-5-20251001"
VOYAGE_MODEL = "voyage-3"

N_EXPAND   = 3
N_RETRIEVE = 40
N_BM25     = 20
N_FINAL    = 25

# Thresholds used when deciding whether book coverage is weak.
WEB_SCORE_THRESHOLD    = 0.40
WEB_COVERAGE_THRESHOLD = 0.40

BOOK_INDEX_PATH = Path(BRAIN_DIR) / "book_index.json"
BM25_CORPUS     = Path(BRAIN_DIR) / "bm25_corpus.json"
MODELS_CONFIG   = Path(__file__).parent / "models_config.json"

_chroma = _text_col = _summary_col = _insight_col = None
_anthropic = _voyage = None
_book_index = None
_bm25_index = _bm25_ids = _bm25_texts = None
_models_config: dict = {}


def init():
    global _chroma, _text_col, _summary_col, _insight_col
    global _anthropic, _voyage, _book_index, _bm25_index, _bm25_ids, _bm25_texts


    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    voyage_key    = os.environ.get("VOYAGE_API_KEY")
    if not anthropic_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set. Add to .env file.")
    if not voyage_key:
        raise EnvironmentError("VOYAGE_API_KEY not set. Add to .env file.")

    _anthropic = anthropic.Anthropic(api_key=anthropic_key)
    _voyage    = voyageai.Client(api_key=voyage_key)

    _chroma = chromadb.PersistentClient(path=BRAIN_DIR)
    _text_col    = _chroma.get_or_create_collection(
        "book_chunks_v2", metadata={"hnsw:space": "cosine"})
    _summary_col = _chroma.get_or_create_collection(
        "book_summaries_v2", metadata={"hnsw:space": "cosine"})
    _insight_col = _chroma.get_or_create_collection(
        "earned_insights", metadata={"hnsw:space": "cosine"})

    if BOOK_INDEX_PATH.exists():
        with open(BOOK_INDEX_PATH, encoding="utf-8") as f:
            _book_index = json.load(f)
    else:
        _book_index = {}

    _bm25_index, _bm25_ids, _bm25_texts = _build_bm25()

    brave_status = "ready" if os.environ.get("BRAVE_API_KEY") else "not configured"
    print(f"  Brain: {_text_col.count():,} vectors · "
          f"{_insight_col.count()} insights · {len(_book_index)} books · "
          f"BM25: {'ready' if _bm25_index else 'not found'} · "
          f"Web search: {brave_status}")
    global _models_config
    if MODELS_CONFIG.exists():
        with open(MODELS_CONFIG, encoding="utf-8") as f:
            _models_config = json.load(f)


def _build_bm25():
    if not BM25_CORPUS.exists():
        return None, None, None
    try:
        from rank_bm25 import BM25Okapi
        with open(BM25_CORPUS, encoding="utf-8") as f:
            corpus = json.load(f)
        texts     = corpus["texts"]
        ids       = corpus["ids"]
        tokenised = [t.lower().split() for t in texts]
        return BM25Okapi(tokenised), ids, texts
    except ImportError:
        print("  [!] rank-bm25 not installed. Run: pip install rank-bm25")
        return None, None, None
    except Exception as e:
        print(f"  [!] BM25 build error: {e}")
        return None, None, None


# ── Intent classification ─────────────────────────────────────────────────────

CONVERSATIONAL_PATTERNS = {
    "greetings": ["hello","hi","hey","howdy","hiya","yo","sup","greetings",
                  "good morning","good afternoon","good evening"],
    "readiness": ["are you ready","shall we begin","let's start","let's go",
                  "ready?","you there","you ready"],
    "thanks":    ["thanks","thank you","cheers","appreciate","great",
                  "awesome","cool","nice","perfect","good job"],
    "meta":      ["what can you do","help me","how do you work",
                  "what are you","who are you","how does this work"],
}

def classify_intent(question: str) -> dict:
    q = question.strip().lower()
    if len(q.split()) <= 2:
        for cat, patterns in CONVERSATIONAL_PATTERNS.items():
            if any(p in q for p in patterns):
                return {"intent": "conversational", "response": _convo_reply(cat)}
    for cat, patterns in CONVERSATIONAL_PATTERNS.items():
        if any(q == p or q.startswith(p) for p in patterns):
            return {"intent": "conversational", "response": _convo_reply(cat)}
    words = q.split()
    if len(words) == 1 and words[0] not in \
            ["why","how","what","when","where","who","which"]:
        return {"intent": "conversational", "response": "Ready. Ask anything."}
    return {"intent": "knowledge"}


def _convo_reply(cat: str) -> str:
    replies = {
        "greetings": "Hey! Ready to dig in. What do you want to explore?",
        "readiness": f"Ready. {_text_col.count():,} vectors across "
                     f"{len(_book_index)} books — ask away.",
        "thanks":    "Good. What's next?",
        "meta":      f"I search {len(_book_index)} books using hybrid retrieval "
                     f"(vector + keyword), then synthesise answers with full citations. "
                     f"Ask anything — I'll draw on your books and tell you exactly where each answer comes from.",
    }
    return replies.get(cat, "Ready.")


# ── Model routing ─────────────────────────────────────────────────────────────

def load_models_config() -> dict:
    return _models_config


def call_model(model_id: str, system: str, messages: list, max_tokens: int = 6000) -> str:
    cfg_data = load_models_config()
    if model_id.startswith("claude"):
        resp = _anthropic.messages.create(
            model=model_id, max_tokens=max_tokens,
            system=system, messages=messages,
        )
        return resp.content[0].text
    elif model_id.startswith("gpt"):
        try:
            from openai import OpenAI
            key = cfg_data.get("openai_api_key") or os.environ.get("OPENAI_API_KEY","")
            if not key: return "⚠ OpenAI API key not set."
            resp = OpenAI(api_key=key).chat.completions.create(
                model=model_id,
                messages=[{"role":"system","content":system}]+messages,
                max_tokens=max_tokens)
            return resp.choices[0].message.content
        except ImportError: return "⚠ Run: pip install openai"
    elif model_id.startswith("deepseek"):
        try:
            from openai import OpenAI
            key = cfg_data.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY","")
            if not key: return "⚠ DeepSeek API key not set."
            resp = OpenAI(api_key=key, base_url="https://api.deepseek.com").chat.completions.create(
                model=model_id,
                messages=[{"role":"system","content":system}]+messages,
                max_tokens=max_tokens)
            return resp.choices[0].message.content
        except ImportError: return "⚠ Run: pip install openai"
    elif model_id.startswith("gemini"):
        try:
            import google.generativeai as genai
            key = cfg_data.get("google_api_key") or os.environ.get("GOOGLE_API_KEY","")
            if not key: return "⚠ Google API key not set."
            genai.configure(api_key=key)
            model = genai.GenerativeModel(model_name=model_id, system_instruction=system)
            full = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
            return model.generate_content(full).text
        except ImportError: return "⚠ Run: pip install google-generativeai"
    return f"⚠ Unknown model: {model_id}"


# ── Web search (Brave) ────────────────────────────────────────────────────────

def web_search(question: str, num_results: int = 5) -> list[dict]:
    """
    Search Brave and return structured results.
    Returns [] if BRAVE_API_KEY not set or request fails.
    """
    brave_key = os.environ.get("BRAVE_API_KEY", "")
    if not brave_key:
        return []
    try:
        params = urllib.parse.urlencode({"q": question, "count": num_results})
        url    = f"https://api.search.brave.com/res/v1/web/search?{params}"
        req    = urllib.request.Request(url, headers={
            "X-Subscription-Token": brave_key,
            "Accept":               "application/json",
            "Accept-Encoding":      "gzip",
        })
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read()
            # Handle gzip
            if resp.info().get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw)

        results = []
        for r in data.get("web", {}).get("results", [])[:num_results]:
            results.append({
                "title":       r.get("title", ""),
                "url":         r.get("url", ""),
                "description": r.get("description", ""),
            })
        return results
    except Exception as e:
        return []


def should_web_search_auto(top_chunks: list[dict],
                            books_searched: int,
                            books_with_results: int) -> bool:
    """
    Use retrieval confidence signals to decide if books have weak coverage.
    """
    if not top_chunks or books_searched == 0:
        return True
    top_score = max(
        (c.get("rerank_score", c.get("rrf_score", c.get("score", 0))) for c in top_chunks),
        default=0
    )
    coverage = books_with_results / books_searched
    return top_score < WEB_SCORE_THRESHOLD and coverage < WEB_COVERAGE_THRESHOLD


def format_web_context(results: list[dict]) -> str:
    """Format Brave results as a context block for synthesis."""
    if not results:
        return ""
    lines = ["=== WEB SOURCES (live search — cite as [Web: title]) ==="]
    for r in results:
        lines.append(
            f"\n[Web: {r['title']}]\n"
            f"URL: {r['url']}\n"
            f"{r['description']}"
        )
    return "\n".join(lines)


# ── Core retrieval pipeline (shared by ask and ask_stream) ────────────────────

def expand_query(question: str) -> list[str]:
    prompt = f"""You are a search query specialist.

The user asked: "{question}"

Generate {N_EXPAND} different search queries covering all semantic angles.
Use different vocabulary and approach different aspects each time.
Return ONLY the queries, one per line, no numbering."""

    resp = _anthropic.messages.create(
        model=EXPAND_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    lines = [l.strip() for l in resp.content[0].text.strip().split("\n") if l.strip()]
    queries = lines[:N_EXPAND]
    if question not in queries:
        queries.insert(0, question)
    return queries[:N_EXPAND + 1]


def vector_retrieve(queries: list[str], use_insights: bool) -> list[dict]:
    query_embeddings = _voyage.embed(
        queries, model=VOYAGE_MODEL, input_type="query"
    ).embeddings

    seen, chunks = set(), []

    def pull(col, label):
        if col.count() == 0:
            return
        for q_emb in query_embeddings:
            res = col.query(
                query_embeddings=[q_emb],
                n_results=min(N_RETRIEVE, col.count()),
                include=["documents", "metadatas", "distances"],
            )
            for doc, meta, dist, cid in zip(
                res["documents"][0], res["metadatas"][0],
                res["distances"][0], res["ids"][0]
            ):
                score = max(0.0, 1.0 - dist)
                if cid not in seen:
                    seen.add(cid)
                    chunks.append({"id": cid, "text": doc, "metadata": meta,
                                   "score": score, "source_col": label})

    pull(_text_col, "book")
    if use_insights and _insight_col.count() > 0:
        pull(_insight_col, "insight")
    return chunks


def bm25_retrieve(question: str) -> dict:
    if _bm25_index is None:
        return {}
    try:
        tokens = question.lower().split()
        scores = _bm25_index.get_scores(tokens)
        max_s  = max(scores) if max(scores) > 0 else 1.0
        top_i  = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:N_BM25]
        return {_bm25_ids[i]: scores[i] / max_s for i in top_i if scores[i] > 0}
    except Exception:
        return {}


def hybrid_fuse(vector_chunks: list[dict], bm25_scores: dict) -> list[dict]:
    K = 60
    vector_by_id = {c["id"]: c for c in vector_chunks}
    vector_ranks  = {c["id"]: i+1 for i, c in enumerate(
        sorted(vector_chunks, key=lambda x: x["score"], reverse=True)
    )}
    bm25_ranks = {cid: i+1 for i, cid in enumerate(
        sorted(bm25_scores, key=lambda x: bm25_scores[x], reverse=True)
    )}

    all_ids      = set(vector_by_id) | set(bm25_scores)
    bm25_only    = [cid for cid in bm25_scores if cid not in vector_by_id]

    # Include keyword-only matches that did not appear in vector retrieval.
    bm25_fetched = {}
    if bm25_only:
        try:
            result = _text_col.get(ids=bm25_only, include=["documents","metadatas"])
            for doc, meta, cid in zip(result["documents"], result["metadatas"], result["ids"]):
                bm25_fetched[cid] = {"id": cid, "text": doc, "metadata": meta,
                                     "score": 0.0, "source_col": "book"}
        except Exception:
            pass

    fused = []
    for cid in all_ids:
        v_rank = vector_ranks.get(cid, len(vector_chunks) + 100)
        b_rank = bm25_ranks.get(cid, len(bm25_scores) + 100)
        rrf    = 1/(K + v_rank) + 1/(K + b_rank)
        if cid in vector_by_id:
            chunk = dict(vector_by_id[cid])
        elif cid in bm25_fetched:
            chunk = dict(bm25_fetched[cid])
        else:
            continue
        chunk["rrf_score"] = rrf
        fused.append(chunk)

    return sorted(fused, key=lambda x: x["rrf_score"], reverse=True)


def rerank(question: str, chunks: list[dict]) -> list[dict]:
    if len(chunks) <= N_FINAL:
        return chunks

    previews = [
        f"[{i}] {c['metadata'].get('short','?')} p.{c['metadata'].get('page','?')}: "
        f"{c['text'][:250].replace(chr(10),' ')}"
        for i, c in enumerate(chunks)
    ]
    prompt = f"""Rate each passage's relevance to: "{question}"
Score 0-10 on how directly it helps answer the question.
Respond ONLY with lines in the format number:score. No preamble, no explanation, no other text.
PASSAGES:\n{chr(10).join(previews)}"""

    try:
        resp = _anthropic.messages.create(
            model=EXPAND_MODEL, max_tokens=500,
            messages=[{"role":"user","content":prompt}],
        )
        for line in resp.content[0].text.strip().split("\n"):
            if ":" in line:
                try:
                    idx, score = line.strip().split(":",1)
                    i = int(idx.strip())
                    if 0 <= i < len(chunks):
                        chunks[i]["rerank_score"] = float(score.strip()) / 10.0
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass

    for c in chunks:
        if "rerank_score" not in c:
            c["rerank_score"] = c.get("rrf_score", c.get("score", 0))

    return sorted(chunks, key=lambda x: x["rerank_score"], reverse=True)[:N_FINAL]


def retrieve(question: str, use_insights: bool, use_web_search: bool) -> dict:
    """
    Full retrieval pipeline. Returns everything needed for synthesis.
    Shared by both ask() and ask_stream().
    """
    queries       = expand_query(question)
    vector_chunks = vector_retrieve(queries, use_insights)
    bm25_scores   = bm25_retrieve(question)
    fused_chunks  = hybrid_fuse(vector_chunks, bm25_scores)
    top_chunks    = rerank(question, fused_chunks)

    books_searched     = len(_book_index)
    books_with_results = len({
        c["metadata"].get("short","?")
        for c in top_chunks
        if c.get("source_col") != "insight"
    })

    # Web search is controlled by the UI toggle.
    web_results  = []
    web_searched = False
    if use_web_search:
        web_results  = web_search(question)
        web_searched = bool(web_results)

    # Build context
    context = _assemble_context(question, top_chunks, use_insights, web_results)

    # Build sources list for UI
    sources = []
    seen = set()
    for c in top_chunks:
        m   = c["metadata"]
        key = f"{m.get('short','?')}_{m.get('page','?')}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "book":       m.get("short","Unknown"),
                "author":     m.get("author",""),
                "page":       m.get("page","?"),
                "type":       m.get("type","text"),
                "score":      round(c.get("rerank_score",c.get("rrf_score",c.get("score",0))),3),
                "is_insight": c.get("source_col") == "insight",
                "preview":    c["text"][:200],
                "full_text":  c["text"],
                "fig_file":   m.get("fig_file",""),
            })

    # Web sources for UI (distinct from book sources)
    web_sources = [{"title": r["title"], "url": r["url"],
                    "description": r["description"], "type": "web"}
                   for r in web_results]

    return {
        "queries":           queries,
        "top_chunks":        top_chunks,
        "fused_chunks":      fused_chunks,
        "context":           context,
        "sources":           sources[:20],
        "web_sources":       web_sources,
        "web_searched":      web_searched,
        "books_searched":    books_searched,
        "books_with_results":books_with_results,
    }


def _assemble_context(question: str, chunks: list[dict],
                      use_insights: bool, web_results: list[dict]) -> str:
    l0 = ["=== KNOWLEDGE BASE CONTENTS ==="]
    for short, info in _book_index.items():
        l0.append(f"• {short} ({info['author']}): {info['teaser']}")

    insight_chunks = [c for c in chunks if c.get("source_col") == "insight"]
    book_chunks    = [c for c in chunks if c.get("source_col") != "insight"]

    ins_section = ""
    if use_insights and insight_chunks:
        lines = ["=== EARNED INSIGHTS ==="]
        for c in insight_chunks:
            m = c["metadata"]
            lines.append(f"\n[Insight: {m.get('title','Untitled')}]\n{c['text']}")
        ins_section = "\n".join(lines)

    summaries = []
    try:
        q_emb = _voyage.embed([question], model=VOYAGE_MODEL, input_type="query").embeddings[0]
        res = _summary_col.query(query_embeddings=[q_emb],
                                 n_results=min(6, _summary_col.count()),
                                 include=["documents","metadatas"])
        for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
            summaries.append(f"--- {meta.get('short','?')} ({meta.get('author','?')}) ---\n{doc}")
    except Exception:
        pass

    l2 = ["=== RELEVANT PASSAGES ==="]
    for c in book_chunks:
        m = c["metadata"]
        t = m.get("type","text")
        if t == "image":
            label = f"[Figure {m.get('fig_idx','?')} | {m.get('short','?')} | Page {m.get('page','?')}]"
        elif t == "table":
            label = f"[Table {m.get('table_idx','?')} | {m.get('short','?')} | Page {m.get('page','?')}]"
        else:
            label = f"[{m.get('short','?')} | Page {m.get('page','?')}]"
        l2.append(f"\n{label}\n{c['text']}")

    parts = ["\n".join(l0)]
    if ins_section:       parts.append(ins_section)
    if summaries:         parts.append("\n\n".join(summaries))
    parts.append("\n".join(l2))
    if web_results:       parts.append(format_web_context(web_results))
    return "\n\n".join(parts)


# ── System prompts ────────────────────────────────────────────────────────────

_TABLE_RULE = """
TABLE FORMATTING — strictly follow:
- ALWAYS use markdown pipe-format tables: | Header | Header |
- NEVER use ASCII art tables (no +---+ borders)
- NEVER put tables inside code blocks
- NEVER output raw HTML tags in your response. Use only plain markdown.
- Mark reasoning as: [reasoning] — plain text, never HTML tags.
"""
_LENGTH_RULE = """
LENGTH AND DENSITY:
- Match depth to question complexity. Simple factual questions: 2-4 paragraphs. Complex technical questions: full structured answer with sections.
- Every sentence must add new information. Never restate the question. Never summarise what was just said at the end.
- If you find yourself repeating a point, delete it. Density over length.
- Do not pad answers with transitional summaries or closing remarks.
"""

_WEB_CITE_RULE = """
WEB SOURCE CITATIONS:
- If web sources are provided, cite them as: [Web: source title]
- Web citations should clearly indicate they are from live web search, not books
"""

EXPLORE_SYSTEM = """You are an expert knowledge synthesis engine with access to a curated library of books.

Give the clearest, most accurate, most useful answer — drawing on the book passages provided and your own knowledge.

CITATION RULES:
- Cite every factual claim from the books inline: [Book Name, p.XX]
- Cite figures: [Figure N, Book Name, p.XX]
- Cite tables: [Table N, Book Name, p.XX]
- When drawing on your own knowledge beyond the books: [reasoning]
- Never fabricate page numbers. Only cite passages actually provided.
- If books don't cover something, say so and draw on general knowledge, clearly labelled.
""" + _TABLE_RULE + _WEB_CITE_RULE + _LENGTH_RULE + """
FORMAT:
- Use ## for major sections, ### for subsections
- Use bullet points for lists
- End with: "📚 Sources:" listing all cited works"""

def _build_system(books_searched: int, books_with_results: int,
                  web_searched: bool) -> str:
    system = EXPLORE_SYSTEM
    if books_searched > 0 and books_with_results < books_searched:
        system += (f"\n\nNOTE: {books_searched} books searched, "
                   f"only {books_with_results} had relevant passages. "
                   f"Clearly distinguish book-sourced from general knowledge.")
    if web_searched:
        system += "\n\nWeb search results are included in the context above. Use them to supplement book knowledge where relevant."
    return system


def _build_messages(question: str, context: str, history: Optional[list]) -> list:
    messages = []
    if history:
        for t in history[-12:]:
            messages.append({"role": t["role"], "content": t["content"]})
    messages.append({"role":"user","content":f"CONTEXT:\n{context}\n\n---\nQUESTION:\n{question}"})
    return messages


# ── Streaming synthesis ───────────────────────────────────────────────────────

def stream_synthesis(question: str, context: str, mode: str,
                     history: Optional[list], model_id: str,
                     books_searched: int, books_with_results: int,
                     web_searched: bool) -> Generator:
    """
    Yields ('token', text) tuples during streaming,
    then ('stop_reason', reason) at end.
    """
    system   = _build_system(books_searched, books_with_results, web_searched)
    messages = _build_messages(question, context, history)

    if model_id.startswith("claude"):
        stop_reason = "end_turn"
        with _anthropic.messages.stream(
            model=model_id, max_tokens=6000,
            system=system, messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield ("token", text)
            try:
                stop_reason = stream.get_final_message().stop_reason
            except Exception:
                pass
        yield ("stop_reason", stop_reason)
    else:
        result = call_model(model_id, system, messages)
        yield ("token", result)
        yield ("stop_reason", "end_turn")


# ── Public interfaces ─────────────────────────────────────────────────────────

def ask(question: str, mode: str = "explore",
        history: Optional[list] = None,
        use_insights: bool = True,
        use_web_search: bool = False,
        model_id: str = "claude-sonnet-4-5") -> dict:
    """Non-streaming interface — unchanged for backward compatibility."""

    intent = classify_intent(question)
    if intent["intent"] == "conversational":
        return {
            "answer": intent["response"], "sources": [], "web_sources": [],
            "queries_used": [], "chunks_retrieved": 0, "chunks_used": 0,
            "mode": "conversational", "model_used": "local",
            "books_searched": 0, "books_with_results": 0,
            "web_searched": False, "is_conversational": True,
        }

    r      = retrieve(question, use_insights, use_web_search)
    system = _build_system(r["books_searched"], r["books_with_results"], r["web_searched"])
    msgs   = _build_messages(question, r["context"], history)
    answer = call_model(model_id, system, msgs)

    return {
        "answer":             answer,
        "sources":            r["sources"],
        "web_sources":        r["web_sources"],
        "queries_used":       r["queries"],
        "chunks_retrieved":   len(r["fused_chunks"]),
        "chunks_used":        len(r["top_chunks"]),
        "mode":               mode,
        "model_used":         model_id,
        "books_searched":     r["books_searched"],
        "books_with_results": r["books_with_results"],
        "web_searched":       r["web_searched"],
        "is_conversational":  False,
    }


def ask_stream(question: str, mode: str = "explore",
               history: Optional[list] = None,
               use_insights: bool = True,
               use_web_search: bool = False,
               model_id: str = "claude-sonnet-4-5") -> Generator[dict, None, None]:
    """
    Streaming interface. Yields dicts with 'event' and 'data' keys.

    Events:
      conversational — short reply, no retrieval
      sources        — retrieval complete, sources ready (fires before synthesis)
      token          — one text token from streaming synthesis
      done           — synthesis complete, includes session metadata
      error          — something went wrong
    """
    intent = classify_intent(question)
    if intent["intent"] == "conversational":
        yield {"event": "conversational", "data": {"text": intent["response"]}}
        return

    try:
        # Retrieval completes before answer synthesis so sources can render first.
        r = retrieve(question, use_insights, use_web_search)

        # Let the UI populate the citation panel before synthesis starts.
        yield {
            "event": "sources",
            "data": {
                "sources":            r["sources"],
                "web_sources":        r["web_sources"],
                "queries_used":       r["queries"],
                "chunks_retrieved":   len(r["fused_chunks"]),
                "chunks_used":        len(r["top_chunks"]),
                "books_searched":     r["books_searched"],
                "books_with_results": r["books_with_results"],
                "web_searched":       r["web_searched"],
                "mode":               mode,
                "model_used":         model_id,
            }
        }

        # Stream answer text after retrieval metadata has been sent.
        for event_type, event_data in stream_synthesis(
            question, r["context"], mode, history, model_id,
            r["books_searched"], r["books_with_results"], r["web_searched"]
        ):
            if event_type == "token":
                yield {"event": "token", "data": {"text": event_data}}
            elif event_type == "stop_reason":
                yield {"event": "done", "data": {"stop_reason": event_data}}

    except Exception as e:
        yield {"event": "error", "data": {"message": str(e)}}


# ── Earned Insights ───────────────────────────────────────────────────────────

def draft_insight(conversation_excerpt: str, user_note: str = "") -> dict:
    prompt = f"""Distil a key insight from this conversation.

{f"User note: {user_note}" if user_note else ""}

Conversation:
{conversation_excerpt}

Respond ONLY with valid JSON (no markdown fences):
{{
  "title": "Short memorable title (5-8 words)",
  "content": "2-4 dense paragraphs: the insight, reasoning, why it matters.",
  "tags": ["tag1", "tag2", "tag3"]
}}"""

    resp = _anthropic.messages.create(
        model="claude-sonnet-4-5", max_tokens=800,
        messages=[{"role":"user","content":prompt}],
    )
    text = resp.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(text)
    except Exception:
        return {"title":"Untitled Insight","content":text,"tags":[]}


def refine_insight(current_draft: dict, user_instruction: str) -> dict:
    prompt = f"""Earned Insight to refine:
Title: {current_draft['title']}
Content: {current_draft['content']}
Tags: {', '.join(current_draft.get('tags',[]))}
Instruction: "{user_instruction}"
Respond ONLY with valid JSON (no markdown fences):
{{"title":"...","content":"...","tags":["..."]}}"""

    resp = _anthropic.messages.create(
        model="claude-sonnet-4-5", max_tokens=800,
        messages=[{"role":"user","content":prompt}],
    )
    text = resp.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(text)
    except Exception:
        return current_draft


def store_insight_in_chroma(insight_id: str, title: str, content: str,
                             tags: list, session_id: str):
    from datetime import datetime
    text      = f"[EARNED INSIGHT: {title}]\n{content}"
    embedding = _voyage.embed([text], model=VOYAGE_MODEL, input_type="document").embeddings[0]
    _insight_col.upsert(
        documents=[text], embeddings=[embedding], ids=[insight_id],
        metadatas=[{
            "title":      title, "tags": ", ".join(tags),
            "session_id": session_id,
            "saved_at":   datetime.now().isoformat(),
            "type":       "earned_insight",
            "short":      f"Insight: {title}",
        }],
    )
