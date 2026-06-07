"""
Inkwell — Ingestion Pipeline v9
========================================
Vision modes:
  "skip"   — no image analysis (default, zero vision cost)
  "claude" — Claude Haiku Vision (best quality, ~$0.002/image)
  "gemini" — Gemini 1.5 Flash Vision (free tier, good quality)

Set in brain_config.json:  "vision_mode": "skip" | "claude" | "gemini"
Override via CLI flag:      python ingest.py --vision=claude

Usage:
    python ingest.py
    python ingest.py --vision=claude
    python ingest.py --resume
    python ingest.py --resume --vision=gemini
"""

import os, sys, json, base64, time, re, argparse, hashlib
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import fitz
import pdfplumber
import chromadb
import anthropic
import voyageai

import config as cfg

PROJECT   = cfg.load()
BOOKS_DIR = PROJECT["books_dir"]
BRAIN_DIR = PROJECT["brain_dir"]

MIN_CHUNK_WORDS = 60
MAX_CHUNK_WORDS = 550
MIN_IMG_DIM     = 100
MAX_IMGS_PAGE   = 5
VOYAGE_MODEL    = "voyage-3"
VOYAGE_BATCH    = 80

CLAUDE_VISION_MODEL = "claude-haiku-4-5"
GEMINI_VISION_MODEL = "gemini-1.5-flash"
SUMMARY_MODEL       = "claude-haiku-4-5"

PROGRESS_FILE      = Path(BRAIN_DIR) / "ingestion_progress.json"
BM25_CORPUS        = Path(BRAIN_DIR) / "bm25_corpus.json"
FIGURES_DIR        = Path(BRAIN_DIR) / "figures"
USER_REGISTRY_FILE = Path(BOOKS_DIR) / "user_book_registry.json"

BLANK_IMAGE_SIGNALS = [
    "blank", "corrupted", "placeholder", "no discernible",
    "cannot determine", "cannot be determined", "no content",
    "no visible", "empty", "unreadable", "illegible",
    "no text", "no figure", "image appears to be",
    "re-upload", "re-submit", "cannot provide",
    "not visible", "unclear image", "no meaningful",
]


# ── Vision mode ───────────────────────────────────────────────────────────────

def resolve_vision_mode(cli_arg: Optional[str]) -> str:
    if cli_arg:
        mode = cli_arg.lower().strip()
        if mode not in ("skip", "claude", "gemini"):
            print(f"[WARNING] Unknown vision mode '{cli_arg}'. Using 'skip'.")
            return "skip"
        return mode
    return PROJECT.get("vision_mode", "skip").lower()


def describe_image_claude(client, img_bytes: bytes, book_meta: dict,
                           page: int, fig_idx: int) -> Optional[str]:
    b64        = base64.standard_b64encode(img_bytes).decode()
    media_type = "image/jpeg" if img_bytes[:2] == b'\xff\xd8' else "image/png"
    prompt = f"""This image is from page {page} of "{book_meta['full_title']}" by {book_meta['author']}.
Describe exactly what you can see. Be specific and factual.
- Diagram or chart: type, axes/labels, data shown, what a reader learns from it.
- Photograph: what is shown, objects visible, context.
- Table: structure, column headers, data contained.
- Equation or formula: write it out, explain variables.
- Blank, decorative, logo, badge, watermark: say exactly that.
Do not relate to any project. Just describe what is there."""
    try:
        resp = client.messages.create(
            model=CLAUDE_VISION_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return resp.content[0].text
    except Exception as e:
        print(f"      [!] Claude Vision error p.{page}: {e}")
        return None


def describe_image_gemini(img_bytes: bytes, book_meta: dict,
                           page: int, fig_idx: int) -> Optional[str]:
    try:
        import google.generativeai as genai
        import PIL.Image, io
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            print("      [!] GOOGLE_API_KEY not set — cannot use Gemini vision.")
            return None
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_VISION_MODEL)
        img   = PIL.Image.open(io.BytesIO(img_bytes))
        prompt = f"""This image is from page {page} of "{book_meta['full_title']}" by {book_meta['author']}.
Describe exactly what you can see. Be specific and factual.
- Diagram or chart: type, axes/labels, data shown, what a reader learns from it.
- Photograph: what is shown, objects visible, context.
- Table: structure, column headers, data contained.
- Equation or formula: write it out, explain variables.
- Blank, decorative, logo, badge, watermark: say exactly that.
Do not relate to any project. Just describe what is there."""
        resp = model.generate_content([prompt, img])
        return resp.text
    except ImportError:
        print("      [!] google-generativeai or Pillow not installed.")
        print("          Run: pip install google-generativeai Pillow")
        return None
    except Exception as e:
        print(f"      [!] Gemini Vision error p.{page}: {e}")
        return None


def describe_image(vision_mode: str, anthropic_client,
                   img_bytes: bytes, book_meta: dict,
                   page: int, fig_idx: int) -> Optional[str]:
    if vision_mode == "skip":
        return None
    elif vision_mode == "claude":
        return describe_image_claude(anthropic_client, img_bytes, book_meta, page, fig_idx)
    elif vision_mode == "gemini":
        return describe_image_gemini(img_bytes, book_meta, page, fig_idx)
    return None


def is_blank_description(description: str) -> bool:
    return any(s in description.lower() for s in BLANK_IMAGE_SIGNALS)


# ── Book metadata ─────────────────────────────────────────────────────────────

def load_user_registry() -> dict:
    if USER_REGISTRY_FILE.exists():
        with open(USER_REGISTRY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_user_registry(registry: dict):
    with open(USER_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def extract_metadata_from_pdf(pdf_path: Path, client) -> dict:
    stem = pdf_path.stem
    try:
        with fitz.open(str(pdf_path)) as doc:
            meta   = doc.metadata or {}
            title  = (meta.get("title")  or "").strip()
            author = (meta.get("author") or "").strip()
            first_page_text = ""
            if len(doc) > 0:
                first_page_text = doc[0].get_text("text")[:3000].strip()
                if len(first_page_text) < 200 and len(doc) > 1:
                    first_page_text = doc[1].get_text("text")[:3000].strip()
        if title and len(title) > 5 and author and len(author) > 2:
            print(f"     [meta] {title} — {author}")
            return _build_meta(title, author)
    except Exception as e:
        print(f"     [!] PDF metadata read error: {e}")
        first_page_text = ""

    if first_page_text:
        try:
            prompt = f"""Extract the book title and author(s) from this text taken from the first pages of a PDF book.

TEXT:
{first_page_text[:2000]}

Respond ONLY with valid JSON, no other text:
{{"title": "full book title here", "author": "Author Name(s) here"}}

Rules:
- Use the full official title
- For multiple authors use "A, B and C" format
- If genuinely cannot determine, use empty string ""
- Never guess or invent"""
            resp = client.messages.create(
                model=CLAUDE_VISION_MODEL, max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            raw  = resp.content[0].text.strip()
            raw  = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(raw)
            title  = (data.get("title")  or "").strip()
            author = (data.get("author") or "").strip()
            if title and len(title) > 3:
                print(f"     [haiku] {title} — {author or 'Unknown'}")
                return _build_meta(title, author or "Unknown")
        except Exception as e:
            print(f"     [!] Haiku extraction error: {e}")

    title = re.sub(r'\s*\(.*?\)\s*', ' ', stem).strip()
    title = re.sub(r'[_\-]+', ' ', title).strip()
    print(f"     [filename] {title}")
    return _build_meta(title, "Unknown")


def _build_meta(full_title: str, author: str) -> dict:
    words = full_title.split()
    short_title = " ".join(words[:6]) if len(words) > 6 else full_title
    short_title = short_title[:50].strip()
    surname = author.split(",")[0].split(" ")[-1] if author and author != "Unknown" else ""
    short = f"{surname} — {short_title}" if surname else short_title
    return {"full_title": full_title, "short": short[:60], "author": author}


def get_book_meta(pdf_path: Path, client, user_registry: dict) -> dict:
    stem = pdf_path.stem
    if stem in user_registry:
        override = user_registry[stem]
        meta = {
            "full_title": override.get("full_title", stem),
            "short":      override.get("short",      stem[:60]),
            "author":     override.get("author",     "Unknown"),
        }
        print(f"     [user registry] {meta['short']} — {meta['author']}")
        return meta
    meta = extract_metadata_from_pdf(pdf_path, client)
    user_registry[stem] = meta
    save_user_registry(user_registry)
    return meta


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_documents(voyage_client, texts: list[str]) -> list[list[float]]:
    """Embed documents with friendly error messages for common failures."""
    all_embeddings = []
    for i in range(0, len(texts), VOYAGE_BATCH):
        batch = texts[i:i + VOYAGE_BATCH]
        try:
            result = voyage_client.embed(batch, model=VOYAGE_MODEL, input_type="document")
            all_embeddings.extend(result.embeddings)
        except Exception as e:
            err = str(e).lower()
            if "rate limit" in err or "payment" in err or "billing" in err or "429" in err:
                print("")
                print("  ⚠  VOYAGE AI RATE LIMIT REACHED")
                print("     Your free tier allows 3 requests/minute without a payment method.")
                print("     To continue: add a payment method at https://dashboard.voyageai.com/")
                print("     You will NOT be charged — 200M free tokens apply first.")
                print("     After adding a card, limits increase within a few minutes.")
                print("     Then re-run with --resume to continue where you left off.")
                print("")
            raise
        if i + VOYAGE_BATCH < len(texts):
            time.sleep(0.2)
    return all_embeddings


# ── Semantic chunking ─────────────────────────────────────────────────────────

def semantic_chunks_from_page(page, page_num: int, source_meta: dict) -> list[dict]:
    blocks = page.get_text("blocks")
    text_blocks = [b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()]
    if not text_blocks:
        return []

    chunks, current_blocks, current_words, chunk_idx = [], [], 0, 0

    for block in text_blocks:
        block_words = len(block.split())
        if block_words > MAX_CHUNK_WORDS:
            if current_blocks:
                text = " ".join(current_blocks)
                if len(text.split()) >= MIN_CHUNK_WORDS:
                    chunks.append(_make_chunk(text, page_num, source_meta, chunk_idx))
                    chunk_idx += 1
                current_blocks, current_words = [], 0
            sentences = re.split(r'(?<=[.!?])\s+', block)
            sub_current, sub_words = [], 0
            for sent in sentences:
                sw = len(sent.split())
                if sub_words + sw > MAX_CHUNK_WORDS and sub_current:
                    text = " ".join(sub_current)
                    if len(text.split()) >= MIN_CHUNK_WORDS:
                        chunks.append(_make_chunk(text, page_num, source_meta, chunk_idx))
                        chunk_idx += 1
                    sub_current, sub_words = [sent], sw
                else:
                    sub_current.append(sent)
                    sub_words += sw
            if sub_current:
                text = " ".join(sub_current)
                if len(text.split()) >= MIN_CHUNK_WORDS:
                    chunks.append(_make_chunk(text, page_num, source_meta, chunk_idx))
                    chunk_idx += 1
            continue

        if current_words + block_words > MAX_CHUNK_WORDS and current_blocks:
            text = " ".join(current_blocks)
            if len(text.split()) >= MIN_CHUNK_WORDS:
                chunks.append(_make_chunk(text, page_num, source_meta, chunk_idx))
                chunk_idx += 1
            current_blocks, current_words = [], 0

        current_blocks.append(block)
        current_words += block_words

    if current_blocks:
        text = " ".join(current_blocks)
        if len(text.split()) >= MIN_CHUNK_WORDS:
            chunks.append(_make_chunk(text, page_num, source_meta, chunk_idx))

    return chunks


def _make_chunk(text: str, page_num: int, source_meta: dict, idx: int) -> dict:
    cid = hashlib.md5(f"{source_meta['short']}_p{page_num}_{idx}".encode()).hexdigest()
    return {
        "id": cid, "text": text,
        "metadata": {**source_meta, "page": page_num, "chunk_idx": idx, "type": "text"},
    }


def table_to_text(table) -> str:
    return "\n".join(
        " | ".join(str(c).strip() if c else "" for c in row)
        for row in (table or [])
    )


# ── Book summary ──────────────────────────────────────────────────────────────

def generate_book_summary(client, book_meta: dict, sample_texts: list[str]) -> str:
    sample = "\n\n---\n\n".join(sample_texts[:20])
    prompt = f"""You are reading excerpts from "{book_meta['full_title']}" by {book_meta['author']}.

Write a 400–500 word summary covering:
1. The book's central thesis and purpose
2. Main topics, frameworks, and methodologies
3. The professional audience it targets
4. 4–5 most important concepts or insights
5. What types of questions this book is well-equipped to answer

Write neutrally based only on the excerpts. No project framing.

EXCERPTS:
{sample[:6000]}"""
    try:
        resp = client.messages.create(
            model=SUMMARY_MODEL, max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception:
        return f"Summary unavailable. {book_meta['full_title']} by {book_meta['author']}."


# ── Progress ──────────────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed_books": [], "stats": {}}


def save_progress(p: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f, indent=2)


# ── Per-book ingestion ────────────────────────────────────────────────────────

def ingest_pdf(pdf_path: Path, text_col, summary_col,
               anthropic_client, voyage_client,
               bm25_accumulator: dict, user_registry: dict,
               vision_mode: str) -> dict:

    print(f"\n{'='*60}")
    print(f"  File   : {pdf_path.name}")
    print(f"  Vision : {vision_mode.upper()}")
    print(f"  Extracting metadata…")

    book_meta = get_book_meta(pdf_path, anthropic_client, user_registry)
    stats = {"text_chunks": 0, "images": 0, "images_rejected": 0,
             "tables": 0, "pages": 0}

    print(f"  Book   : {book_meta['short']}")
    print(f"  Author : {book_meta['author']}")
    print(f"{'='*60}")

    all_texts, all_ids, all_metas = [], [], []
    sample_texts = []

    # ── Text ──────────────────────────────────────────────────────────────────
    print("  [1/3] Text (semantic blocks)...")
    with fitz.open(str(pdf_path)) as doc:
        stats["pages"] = len(doc)
        for page_num, page in enumerate(doc, start=1):
            for c in semantic_chunks_from_page(page, page_num, book_meta):
                all_texts.append(c["text"])
                all_ids.append(c["id"])
                all_metas.append(c["metadata"])
                if len(sample_texts) < 30:
                    sample_texts.append(c["text"])
                stats["text_chunks"] += 1

    print(f"     → {stats['text_chunks']} chunks from {stats['pages']} pages")

    # Scanned PDF detection — warn if no text was found
    if stats["text_chunks"] == 0:
        print(f"")
        print(f"  ⚠  WARNING: No text extracted from this PDF.")
        print(f"     This PDF may be a scanned image document with no text layer.")
        print(f"     Inkwell requires PDFs with selectable text.")
        print(f"     To fix: run OCR on the file first (e.g. Adobe Acrobat, OCRmyPDF).")
        print(f"     Skipping this book — it will not be searchable.")
        print(f"")
        return stats

    # ── Images ────────────────────────────────────────────────────────────────
    if vision_mode == "skip":
        print("  [2/3] Images → SKIPPED (vision_mode=skip)")
    else:
        print(f"  [2/3] Images → Vision ({vision_mode})...")
        with fitz.open(str(pdf_path)) as doc:
            for page_num, page in enumerate(doc, start=1):
                imgs = page.get_images(full=True)
                if len(imgs) > MAX_IMGS_PAGE:
                    continue
                for fi, img_ref in enumerate(imgs):
                    try:
                        bi  = doc.extract_image(img_ref[0])
                        ib  = bi["image"]
                        w, h = bi.get("width", 0), bi.get("height", 0)
                        if w < MIN_IMG_DIM or h < MIN_IMG_DIM:
                            continue
                        desc = describe_image(vision_mode, anthropic_client,
                                              ib, book_meta, page_num, fi)
                        if not desc or is_blank_description(desc):
                            print(f"      [skip] p.{page_num} fig{fi+1} — blank/decorative")
                            stats["images_rejected"] += 1
                            continue
                        cid      = hashlib.md5(f"{book_meta['short']}_img_{page_num}_{fi}".encode()).hexdigest()
                        fig_ext  = "jpg" if ib[:2] == b'\xff\xd8' else "png"
                        fig_name = f"{cid}.{fig_ext}"
                        (FIGURES_DIR / fig_name).write_bytes(ib)
                        img_text = f"[FIGURE — {book_meta['short']}, Page {page_num}, Figure {fi+1}]\n{desc}"
                        all_texts.append(img_text)
                        all_ids.append(cid)
                        all_metas.append({**book_meta, "page": page_num,
                                          "fig_idx": fi+1, "type": "image",
                                          "fig_file": fig_name})
                        stats["images"] += 1
                        if vision_mode == "gemini":
                            time.sleep(1.0)
                        else:
                            time.sleep(0.3)
                    except Exception as e:
                        print(f"      [!] p.{page_num}: {e}")
        print(f"     → {stats['images']} stored, {stats['images_rejected']} rejected")

    # ── Tables ────────────────────────────────────────────────────────────────
    print("  [3/3] Tables...")
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                for ti, table in enumerate(page.extract_tables()):
                    txt = table_to_text(table)
                    if len(txt.strip()) < 50:
                        continue
                    cid = hashlib.md5(f"{book_meta['short']}_table_{page_num}_{ti}".encode()).hexdigest()
                    tbl_text = f"[TABLE — {book_meta['short']}, Page {page_num}, Table {ti+1}]\n{txt}"
                    all_texts.append(tbl_text)
                    all_ids.append(cid)
                    all_metas.append({**book_meta, "page": page_num,
                                      "table_idx": ti+1, "type": "table"})
                    stats["tables"] += 1
    except Exception as e:
        print(f"     [!] pdfplumber: {e}")
    print(f"     → {stats['tables']} tables")

    # ── Embed ─────────────────────────────────────────────────────────────────
    print(f"  Embedding {len(all_texts)} chunks with voyage-3...")
    embeddings = embed_documents(voyage_client, all_texts)

    # ── Store ─────────────────────────────────────────────────────────────────
    print(f"  Storing in ChromaDB...")
    for i in range(0, len(all_texts), 100):
        text_col.upsert(
            documents=all_texts[i:i+100],
            embeddings=embeddings[i:i+100],
            ids=all_ids[i:i+100],
            metadatas=all_metas[i:i+100],
        )

    for cid, txt in zip(all_ids, all_texts):
        bm25_accumulator["ids"].append(cid)
        bm25_accumulator["texts"].append(txt)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("  Generating book summary...")
    summary = generate_book_summary(anthropic_client, book_meta, sample_texts)
    sum_emb = embed_documents(voyage_client, [summary])[0]
    sum_id  = hashlib.md5(book_meta["short"].encode()).hexdigest()
    summary_col.upsert(
        documents=[summary], embeddings=[sum_emb], ids=[sum_id],
        metadatas=[{**book_meta, "type": "book_summary"}],
    )
    print(f"     → {len(summary.split())} word summary stored")
    return stats


# ── Book index ────────────────────────────────────────────────────────────────

def build_book_index(summary_col, output_path: Path):
    data  = summary_col.get(include=["documents", "metadatas"])
    index = {}
    for doc, meta in zip(data["documents"], data["metadatas"]):
        sentences = re.split(r'(?<=[.!?])\s+', doc.strip())
        index[meta["short"]] = {
            "author":       meta["author"],
            "teaser":       " ".join(sentences[:2]),
            "full_summary": doc,
        }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"\n  Book index → {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Inkwell Ingestion")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-ingested books")
    parser.add_argument("--vision", type=str, default=None,
                        choices=["skip", "claude", "gemini"],
                        help="Vision mode for image analysis (overrides config)")
    args = parser.parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    voyage_key    = os.environ.get("VOYAGE_API_KEY")

    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set. Add to .env file.")
        sys.exit(1)
    if not voyage_key:
        print("[ERROR] VOYAGE_API_KEY not set. Add to .env file.")
        sys.exit(1)

    vision_mode = resolve_vision_mode(args.vision)

    if vision_mode == "gemini" and not os.environ.get("GOOGLE_API_KEY"):
        print("[ERROR] GOOGLE_API_KEY not set but vision=gemini selected.")
        print("  Add GOOGLE_API_KEY to your .env, or use --vision=claude or --vision=skip")
        sys.exit(1)

    Path(BRAIN_DIR).mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
    voyage_client    = voyageai.Client(api_key=voyage_key)

    chroma      = chromadb.PersistentClient(path=BRAIN_DIR)
    text_col    = chroma.get_or_create_collection(
        "book_chunks_v2", metadata={"hnsw:space": "cosine"})
    summary_col = chroma.get_or_create_collection(
        "book_summaries_v2", metadata={"hnsw:space": "cosine"})

    pdf_files = sorted(Path(BOOKS_DIR).glob("*.pdf"))
    if not pdf_files:
        print(f"[ERROR] No PDFs found in {BOOKS_DIR}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Inkwell — Ingestion")
    print(f"  Project  : {PROJECT['project_name']}")
    print(f"  Embedding: voyage-3 · cosine distance")
    print(f"  Vision   : {vision_mode.upper()}")
    if vision_mode == "skip":
        print(f"  (Images will not be analysed — text and tables only)")
    elif vision_mode == "gemini":
        print(f"  (Using Gemini 1.5 Flash — ensure GOOGLE_API_KEY is set)")
    print(f"  Found    : {len(pdf_files)} PDFs")
    print(f"{'='*60}")
    print(f"\n  Metadata : auto-extracted (PDF meta → Haiku → filename)")
    print(f"  Override : edit {USER_REGISTRY_FILE}\n")

    user_registry = load_user_registry()
    progress = load_progress() if args.resume else {"completed_books": [], "stats": {}}

    if args.resume and BM25_CORPUS.exists():
        with open(BM25_CORPUS, encoding="utf-8") as f:
            bm25_accumulator = json.load(f)
        # Deduplicate in case a partial run left duplicate entries
        seen, ids, texts = set(), [], []
        for cid, txt in zip(bm25_accumulator["ids"], bm25_accumulator["texts"]):
            if cid not in seen:
                seen.add(cid)
                ids.append(cid)
                texts.append(txt)
        dupes = len(bm25_accumulator["ids"]) - len(ids)
        bm25_accumulator = {"ids": ids, "texts": texts}
        msg = f"  BM25: resuming with {len(ids)} chunks"
        if dupes:
            msg += f" ({dupes} duplicate entries removed)"
        print(msg)
    else:
        bm25_accumulator = {"ids": [], "texts": []}

    total = {"text_chunks": 0, "images": 0, "images_rejected": 0, "tables": 0, "pages": 0}

    for idx, pdf_path in enumerate(pdf_files, 1):
        if args.resume and pdf_path.name in progress["completed_books"]:
            print(f"  [{idx}/{len(pdf_files)}] Skip (already done): {pdf_path.name}")
            continue
        print(f"\n  [{idx}/{len(pdf_files)}]")
        try:
            stats = ingest_pdf(pdf_path, text_col, summary_col,
                               anthropic_client, voyage_client,
                               bm25_accumulator, user_registry, vision_mode)
            for k in total:
                total[k] += stats.get(k, 0)
            progress["completed_books"].append(pdf_path.name)
            progress["stats"][pdf_path.name] = stats
            save_progress(progress)
            with open(BM25_CORPUS, "w", encoding="utf-8") as f:
                json.dump(bm25_accumulator, f, ensure_ascii=False)

        except KeyboardInterrupt:
            save_progress(progress)
            with open(BM25_CORPUS, "w", encoding="utf-8") as f:
                json.dump(bm25_accumulator, f, ensure_ascii=False)
            print("\n  Progress saved. Run with --resume to continue.")
            sys.exit(0)
        except Exception as e:
            print(f"  [ERROR] {pdf_path.name}: {e}")

    build_book_index(summary_col, Path(BRAIN_DIR) / "book_index.json")

    print(f"\n{'='*60}")
    print(f"  DONE")
    print(f"  Pages  : {total['pages']:,}    Chunks : {total['text_chunks']:,}")
    if vision_mode != "skip":
        print(f"  Images : {total['images']} stored · {total['images_rejected']} rejected")
    print(f"  Tables : {total['tables']:,}")
    print(f"  BM25   : {len(bm25_accumulator['ids']):,} entries")
    print(f"  Vectors: {text_col.count():,}")
    print(f"\n  Review titles at : {USER_REGISTRY_FILE}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()