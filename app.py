"""
Inkwell — Web App v9
==============================
Fixes in this version:
  - setup_status: checks key presence, not brain_ready (wizard only for missing keys)
  - setup_save: friendly auth error messages
  - ChatRequest: is_continuation flag — skips DB save for continuation turns
  - ingest_stream: emits "safe to close tab" note at start
  - Brain loading: /api/project exposes brain_ready for frontend polling
"""

import os, json, sys, asyncio, uvicorn, re
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config as cfg
import query as brain
import storage

PROJECT       = cfg.load()
MODELS_CONFIG = Path(__file__).parent / "models_config.json"
FIGURES_DIR   = Path(PROJECT["brain_dir"]) / "figures"
BOOKS_DIR     = Path(PROJECT["books_dir"])
ENV_PATH      = Path(__file__).parent / ".env"
LOGO_PATH     = Path(__file__).parent / "inkwell-logo.png"

_brain_ready   = False
_ingest_running = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _brain_ready
    try:
        brain.init()
        storage.init_db()
        _brain_ready = True
        print(f"\n  Inkwell — {PROJECT['project_name']}")
        print(f"  http://localhost:8000\n")
    except EnvironmentError as e:
        print(f"\n  [SETUP NEEDED] {e}")
        print(f"  Open http://localhost:8000 to complete setup.\n")
        _brain_ready = False
    except Exception as e:
        print(f"\n  [ERROR] Brain init failed: {e}\n")
        _brain_ready = False
    yield


app = FastAPI(title="Inkwell", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def require_brain():
    if not _brain_ready:
        raise HTTPException(503, "Brain not ready. Complete setup at http://localhost:8000")


# ── Request models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question:        str
    mode:            str  = "explore"
    history:         Optional[list] = []
    session_id:      Optional[str]  = None
    use_insights:    bool = True
    use_web_search:  bool = False
    model_id:        str  = "claude-sonnet-4-5"
    is_continuation: bool = False   # When True: skip saving to DB

class SetupSaveRequest(BaseModel):
    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY:    str = ""
    BRAVE_API_KEY:     str = ""
    GOOGLE_API_KEY:    str = ""
    OPENAI_API_KEY:    str = ""
    DEEPSEEK_API_KEY:  str = ""

class IngestRequest(BaseModel):
    vision: str  = "skip"
    resume: bool = True

class InsightDraftRequest(BaseModel):
    conversation_excerpt: str
    user_note:            str = ""
    session_id:           str
    turn_number:          int

class InsightRefineRequest(BaseModel):
    current_draft:    dict
    user_instruction: str

class InsightSaveRequest(BaseModel):
    draft:       dict
    session_id:  str
    turn_number: int


# ── Setup endpoints ───────────────────────────────────────────────────────────

@app.get("/api/setup/status")
async def setup_status():
    """
    setup_needed is TRUE only when API keys are genuinely missing.
    brain_ready being False for other reasons (ChromaDB issue, etc.)
    should NOT show the wizard — the frontend handles that separately.
    """
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    has_voyage    = bool(os.environ.get("VOYAGE_API_KEY",    "").strip())
    keys_missing  = not (has_anthropic and has_voyage)
    return {
        "setup_needed":  keys_missing,
        "brain_ready":   _brain_ready,
        "has_anthropic": has_anthropic,
        "has_voyage":    has_voyage,
        "has_brave":     bool(os.environ.get("BRAVE_API_KEY",   "").strip()),
        "has_google":    bool(os.environ.get("GOOGLE_API_KEY",  "").strip()),
        "env_exists":    ENV_PATH.exists(),
    }


@app.post("/api/setup/save")
async def setup_save(req: SetupSaveRequest):
    global _brain_ready

    existing = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    existing[key.strip()] = val.strip()

    for key, val in req.model_dump().items():
        if val and val.strip():
            existing[key] = val.strip()

    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("# Inkwell — API Keys\n")
        for key, val in existing.items():
            f.write(f"{key}={val}\n")

    try:
        load_dotenv(dotenv_path=str(ENV_PATH), override=True)
    except Exception:
        pass

    try:
        brain.init()
        storage.init_db()
        _brain_ready = True
        return {"status": "ready", "message": "Brain initialised successfully."}
    except Exception as e:
        _brain_ready = False
        err = str(e)
        err_lower = err.lower()
        if "anthropic_api_key" in err or "authentication" in err_lower or "invalid api key" in err_lower:
            msg = "Anthropic API key looks incorrect. Double-check it at console.anthropic.com"
        elif "voyage_api_key" in err or "voyage" in err_lower:
            msg = "Voyage AI key looks incorrect. Double-check it at dashboard.voyageai.com"
        else:
            msg = err
        return {"status": "error", "message": msg}


# ── Books endpoints ───────────────────────────────────────────────────────────

@app.get("/api/books")
async def list_books():
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))

    ingested = set()
    progress_path = Path(PROJECT["brain_dir"]) / "ingestion_progress.json"
    if progress_path.exists():
        try:
            with open(progress_path, encoding="utf-8") as f:
                progress = json.load(f)
                ingested = set(progress.get("completed_books", []))
        except Exception:
            pass

    return [
        {
            "name":     p.name,
            "size":     p.stat().st_size,
            "size_mb":  round(p.stat().st_size / 1024 / 1024, 1),
            "ingested": p.name in ingested,
        }
        for p in pdfs
    ]


@app.post("/api/books/upload")
async def upload_book(file: UploadFile = File(...)):
    original_name = Path(file.filename or "").name
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", original_name).strip(" .")
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")
    if not safe_name:
        raise HTTPException(400, "Invalid filename.")
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    dest    = BOOKS_DIR / safe_name
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    return {"filename": safe_name, "size_mb": round(len(content) / 1024 / 1024, 1)}


@app.delete("/api/books/{filename}")
async def delete_book(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename.")
    target = BOOKS_DIR / filename
    if not target.exists():
        raise HTTPException(404, "File not found.")
    target.unlink()
    return {"status": "deleted"}


# ── Ingest streaming ──────────────────────────────────────────────────────────

@app.post("/api/ingest/stream")
async def ingest_stream(req: IngestRequest):
    global _ingest_running
    if _ingest_running:
        raise HTTPException(409, "Ingestion already running.")

    async def generate():
        global _ingest_running, _brain_ready
        _ingest_running = True
        try:
            # Emit a first event immediately so the browser can show progress.
            yield f"data: {json.dumps({'type': 'log', 'text': '  Safe to close this tab — progress saves automatically after each book.'})}\n\n"
            yield f"data: {json.dumps({'type': 'log', 'text': ''})}\n\n"

            try:
                brain.shutdown()
            except Exception:
                pass
            _brain_ready = False

            script = str(Path(__file__).parent / "ingest.py")
            cmd    = [sys.executable, script, f"--vision={req.vision}"]
            if req.resume:
                cmd.append("--resume")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path(__file__).parent),
                env={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
            )

            async for raw_line in proc.stdout:
                text = raw_line.decode("utf-8", errors="replace").rstrip()
                yield f"data: {json.dumps({'type': 'log', 'text': text})}\n\n"

            await proc.wait()
            success = proc.returncode == 0
            yield f"data: {json.dumps({'type': 'done', 'success': success})}\n\n"

            if success:
                try:
                    brain.init()
                    _brain_ready = True
                    yield f"data: {json.dumps({'type': 'reloaded'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'warn', 'text': f'Brain reload failed: {e}'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        finally:
            _ingest_running = False

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Connection":                  "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/ingest/status")
async def ingest_status():
    return {"running": _ingest_running}


@app.post("/api/brain/reload")
async def brain_reload():
    global _brain_ready
    try:
        brain.init()
        _brain_ready = True
        return {
            "status":  "ok",
            "vectors": brain._text_col.count() if brain._text_col else 0,
            "books":   len(brain._book_index) if brain._book_index else 0,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Chat (non-streaming) ──────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(req: ChatRequest):
    require_brain()
    if not req.question.strip():
        raise HTTPException(400, "Question cannot be empty")
    try:
        result = brain.ask(
            question=req.question, mode=req.mode,
            history=req.history, use_insights=req.use_insights,
            use_web_search=req.use_web_search, model_id=req.model_id,
        )
    except Exception as e:
        raise HTTPException(500, str(e))

    if not result.get("is_conversational") and not req.is_continuation:
        session_id = req.session_id or storage.create_session(req.mode)
        turns      = storage.get_session_turns(session_id)
        turn_num   = len(turns) + 1
        if turn_num == 1:
            storage.update_session_title(session_id, req.question)
        storage.save_turn(session_id, turn_num,     "user", req.question, req.mode)
        storage.save_turn(session_id, turn_num + 1, "assistant",
                          result["answer"], req.mode,
                          result["sources"], result["chunks_used"])
        result["session_id"]  = session_id
        result["turn_number"] = turn_num
    else:
        result["session_id"]  = req.session_id
        result["turn_number"] = 0
    return result


# ── Chat streaming ────────────────────────────────────────────────────────────

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    require_brain()
    if not req.question.strip():
        raise HTTPException(400, "Question cannot be empty")

    session_id = req.session_id

    def generate():
        nonlocal session_id
        full_answer    = []
        retrieval_meta = {}

        try:
            for event in brain.ask_stream(
                question=req.question, mode=req.mode,
                history=req.history, use_insights=req.use_insights,
                use_web_search=req.use_web_search, model_id=req.model_id,
            ):
                name = event["event"]
                data = event["data"]
                if name == "sources":
                    retrieval_meta = data
                if name == "token":
                    full_answer.append(data["text"])
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
            return

        # Continuations extend the visible answer without creating a new DB turn.
        if full_answer and retrieval_meta and not req.is_continuation:
            answer_text = "".join(full_answer)
            try:
                if not session_id:
                    session_id = storage.create_session(req.mode)
                turns    = storage.get_session_turns(session_id)
                turn_num = len(turns) + 1
                if turn_num == 1:
                    storage.update_session_title(session_id, req.question)
                storage.save_turn(session_id, turn_num, "user", req.question, req.mode)
                combined = (retrieval_meta.get("sources", []) +
                            retrieval_meta.get("web_sources", []))
                storage.save_turn(session_id, turn_num + 1, "assistant",
                                  answer_text, req.mode,
                                  combined, retrieval_meta.get("chunks_used", 0))
                yield (f"event: session\ndata: "
                       f"{json.dumps({'session_id': session_id, 'turn_number': turn_num})}\n\n")
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Connection":                  "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Figures ───────────────────────────────────────────────────────────────────

@app.get("/api/figures/{filename}")
async def serve_figure(filename: str):
    if not filename.replace("-","").replace("_","").replace(".","").isalnum():
        raise HTTPException(400, "Invalid filename")
    fig_path = FIGURES_DIR / filename
    if not fig_path.exists():
        raise HTTPException(404, "Figure not found")
    media_type = "image/jpeg" if filename.endswith(".jpg") else "image/png"
    return FileResponse(str(fig_path), media_type=media_type)


@app.get("/inkwell-logo.png")
async def serve_logo():
    if not LOGO_PATH.exists():
        raise HTTPException(404, "Logo not found")
    return FileResponse(str(LOGO_PATH), media_type="image/png")


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    if not _brain_ready: return []
    return storage.get_all_sessions()

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    require_brain()
    s = storage.get_session(session_id)
    if not s: raise HTTPException(404, "Session not found")
    return {"session": s, "turns": storage.get_session_turns(session_id)}

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    require_brain()
    storage.delete_session(session_id)
    return {"status": "deleted"}

@app.delete("/api/turns/{turn_id}")
async def delete_turn(turn_id: int):
    require_brain()
    storage.soft_delete_turn(turn_id)
    return {"status": "deleted"}


# ── Insights ──────────────────────────────────────────────────────────────────

@app.post("/api/insights/draft")
async def draft_insight(req: InsightDraftRequest):
    require_brain()
    if not req.conversation_excerpt.strip():
        raise HTTPException(400, "No answer text was available to distil into an insight.")
    try:
        return brain.draft_insight(req.conversation_excerpt, req.user_note)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/insights/refine")
async def refine_insight(req: InsightRefineRequest):
    require_brain()
    try:
        return brain.refine_insight(req.current_draft, req.user_instruction)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/insights/save")
async def save_insight(req: InsightSaveRequest):
    require_brain()
    try:
        d   = req.draft
        iid = storage.save_insight(
            req.session_id, req.turn_number,
            d["title"], d["content"], d.get("tags",[]), in_chroma=True)
        brain.store_insight_in_chroma(
            iid, d["title"], d["content"], d.get("tags",[]), req.session_id)
        storage.mark_insight_in_chroma(iid)
        return {"insight_id": iid, "status": "saved"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/insights")
async def list_insights():
    if not _brain_ready: return []
    return storage.get_all_insights()

@app.delete("/api/insights/{insight_id}")
async def delete_insight(insight_id: str):
    require_brain()
    storage.delete_insight(insight_id)
    return {"status": "deleted"}


# ── Meta ──────────────────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models():
    cfg_data = {}
    if MODELS_CONFIG.exists():
        with open(MODELS_CONFIG) as f:
            cfg_data = json.load(f)
    def has_key(config_key: str, env_key: str) -> bool:
        return bool((cfg_data.get(config_key) or os.environ.get(env_key, "")).strip())
    return {"models": [
        {"id":"claude-sonnet-4-5",        "label":"Claude Sonnet",    "provider":"Anthropic","available":True},
        {"id":"claude-haiku-4-5-20251001","label":"Claude Haiku",     "provider":"Anthropic","available":True,"note":"Faster, cheaper"},
        {"id":"gpt-4o",                   "label":"GPT-4o",           "provider":"OpenAI",   "available":has_key("openai_api_key", "OPENAI_API_KEY")},
        {"id":"gpt-4o-mini",              "label":"GPT-4o Mini",      "provider":"OpenAI",   "available":has_key("openai_api_key", "OPENAI_API_KEY"),"note":"Faster, cheaper"},
        {"id":"deepseek-chat",            "label":"DeepSeek V3",      "provider":"DeepSeek", "available":has_key("deepseek_api_key", "DEEPSEEK_API_KEY"),"note":"Very cheap"},
        {"id":"gemini-1.5-flash",         "label":"Gemini 1.5 Flash", "provider":"Google",   "available":has_key("google_api_key", "GOOGLE_API_KEY"),"note":"Free tier"},
        {"id":"gemini-1.5-pro",           "label":"Gemini 1.5 Pro",   "provider":"Google",   "available":has_key("google_api_key", "GOOGLE_API_KEY")},
    ]}

@app.get("/api/modes")
async def list_modes():
    return {"modes": [
        {"id":"explore","label":"Explore","icon":"🔭",
         "desc":"Deep knowledge from your books + Claude — neutral, cited, dense"},
    ]}

@app.get("/api/project")
async def project_info():
    return {
        "name":             PROJECT["project_name"],
        "description":      PROJECT.get("project_description",""),
        "goals":            PROJECT.get("project_goals",[]),
        "books":            list(brain._book_index.keys()) if brain._book_index else [],
        "total_vectors":    brain._text_col.count()    if brain._text_col    else 0,
        "total_insights":   brain._insight_col.count() if brain._insight_col else 0,
        "web_search_ready": bool(os.environ.get("BRAVE_API_KEY","").strip()),
        "brain_ready":      _brain_ready,
    }

@app.get("/", response_class=HTMLResponse)
async def root():
    p = Path(__file__).parent / "index.html"
    return HTMLResponse(
        p.read_text(encoding="utf-8") if p.exists()
        else "<h1>index.html not found</h1>"
    )

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
