# Inkwell

Inkwell is a local, bring-your-own-key research assistant for PDF libraries. It indexes your books into a local search database, then lets you ask questions and get cited answers back to the exact pages and passages that informed them.

It is built for people who want to think with a curated library instead of asking the open internet first: founders, researchers, operators, students, writers, and domain experts with PDFs worth returning to.

## What It Does

- Indexes PDF books into a local ChromaDB vector database using Voyage embeddings
- Combines semantic search with BM25 keyword search for better retrieval
- Streams answers with inline citations such as `[Book Name, p.42]`
- Shows retrieved source passages in a citation drawer
- Supports optional Brave web search when a Brave key is provided
- Lets you save "Earned Insights" from useful sessions and include them in future answers
- Optionally analyzes figures and diagrams with Claude or Gemini vision
- Stores your books, sessions, insights, and vector database locally

## Status

Inkwell is an early local-first release. The core single-library workflow is usable today: install, add keys, upload PDFs, ingest, ask questions, and inspect citations.

Planned next steps include:

- Multiple project libraries inside one Inkwell installation
- A "Check for updates" flow for Git-based installs
- A more formal release/package flow
- More resilient cross-platform testing

## How It Works

1. You add your own API keys in the setup screen.
2. You upload PDF books from the browser UI.
3. Inkwell extracts text, tables, and optionally figure descriptions.
4. It stores searchable chunks locally in `Inkwell Data/`.
5. When you ask a question, Inkwell retrieves relevant passages and asks the selected model to synthesize an answer with citations.

Your `.env`, books, Chroma database, and session history are ignored by git and should stay on your machine.

## Requirements

- Python 3.11+
- Anthropic API key
- Voyage AI API key

Optional:

- Brave Search API key for live web search
- Google API key for Gemini vision or Gemini synthesis
- OpenAI API key for GPT models
- DeepSeek API key for DeepSeek chat

Provider pricing and free-tier details change over time, so check each provider's pricing page before heavy use. Inkwell is designed as BYOK software: you control your own usage and billing.

## Installation

### Windows

1. Clone or download this repository.
2. Extract it to a simple path such as `C:\Inkwell\`.
3. Double-click `install.bat`.
4. If Windows SmartScreen appears, click **More info** and then **Run anyway**.
5. Wait for dependencies to install.
6. Launch Inkwell from the desktop shortcut or by double-clicking `start.bat`.

### Mac / Linux

```bash
chmod +x install.sh
./install.sh
./start.sh
```

The shell scripts are included for convenience, but the project is currently tested primarily on Windows.

## First Run

1. Launch Inkwell.
2. Your browser opens to `http://localhost:8000`.
3. Enter your Anthropic and Voyage keys in the setup wizard.
4. Add optional provider keys if you want web search or non-Anthropic synthesis models.
5. Open the **Books** tab and upload PDFs.
6. Choose a vision mode and click **Start Ingestion**.
7. Ask questions from your indexed library.

## Adding Books

PDFs should have selectable text. Scanned PDFs without OCR will not extract meaningful text. If a PDF is scanned, run OCR first with a tool such as Adobe Acrobat or OCRmyPDF.

Vision modes:

- **Skip**: text and tables only; recommended for most books
- **Claude Vision**: stronger for technical figures and diagrams
- **Gemini Vision**: useful when you prefer Google's vision model

Keep **Skip already-ingested books** checked when adding new books later.

## Updating

For now, updates are manual:

- If you cloned with git, run `git pull` from the project folder, then reinstall dependencies if `requirements.txt` changed.
- If you downloaded a ZIP, download the new version and copy over your local `.env`, `Books/`, and `Inkwell Data/` folders.

A safer in-app update flow is planned. The intended model is: check GitHub for a newer version, pull changes for Git installs, update dependencies, then ask the user to restart Inkwell. Local data will remain outside git-tracked files.

## Project Structure

```text
app.py              FastAPI backend and API routes
query.py            Retrieval, reranking, model routing, and synthesis
ingest.py           PDF ingestion pipeline
config.py           Project configuration loader
storage.py          SQLite session and insight persistence
index.html          Browser UI
brain_config.json   Local project settings
.env.template       API key template
requirements.txt    Python dependencies
install.bat         Windows installer
install.sh          Mac/Linux installer
start.bat           Windows launcher
start.sh            Mac/Linux launcher
Books/              User PDFs go here
```

## Configuration

`brain_config.json` controls the current library:

```json
{
  "project_name": "My Inkwell",
  "project_description": "My personal research and knowledge library",
  "project_goals": [],
  "vision_mode": "skip",
  "books_dir": "Books",
  "brain_dir": "Inkwell Data"
}
```

The browser setup wizard handles API keys. If you prefer manual setup, copy `.env.template` to `.env` and fill in the keys you want to use.

## Troubleshooting

**The browser opens before the server is ready**

Wait a few seconds and refresh `http://localhost:8000`.

**The setup wizard keeps appearing**

Make sure `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` are saved in `.env`, or enter them again in the setup wizard.

**Ingestion says no text was extracted**

The PDF is probably scanned or image-only. Run OCR first, then ingest again.

**Voyage rate limit during ingestion**

Wait and resume later, or check your Voyage account limits. Re-run ingestion with **Skip already-ingested books** enabled.

**Work or school laptop issues**

Managed devices may block Python installation, PowerShell, local servers, or database files. A personal machine is usually smoother.

## Tech Stack

- FastAPI and uvicorn
- Vanilla HTML/CSS/JavaScript
- ChromaDB
- Voyage embeddings
- BM25 keyword search
- Anthropic, OpenAI-compatible, Google Gemini, and DeepSeek model routing
- SQLite for local sessions and insights

## License

MIT
