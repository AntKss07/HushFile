# HushFile 

> **Drop it in Downloads, forget it, find it later.**

**HushFile** is an autonomous, fully offline desktop background agent that watches your download folder, extracts content using open-weight models, gives files clean and descriptive names, files them into an organized directory structure, and logs every operation in a crash-safe SQLite undo journal.

Any file can be inspected with a Quick-Info card explaining *why* HushFile filed it where it did, and all documents can be retrieved semantically by meaning rather than filename. Everything runs 100% offline on your own machine — marksheets, bills, IDs, medical records, and contracts never leave your laptop.

---

## Key Features

- **Crash-Safe Undo Journal:** Write-ahead logging ensures any mid-move crash is recovered automatically upon next startup. Batch and single-move reversibility with checksum verification and occupied-source non-destructive restoration (`file (restored).ext`).
- **Local AI Pipeline:** Powered by Ollama (`qwen2.5:7b-instruct` / `qwen3:4b`, `qwen2.5vl:3b`, `nomic-embed-text`) with strict Pydantic JSON schema constraints.
- **Robustness & Anti-Prompt-Injection:** Document text is strictly parsed as untrusted data. One automatic retry on malformed outputs with graceful deterministic rule fallbacks.
- **Multi-Format Extraction:** PyMuPDF (with OCR fallback for scanned pages), Python-docx, OpenPyXL, CSV, Tesseract OCR for images (with VLM caption fallback), faster-whisper for audio, and syntax-aware text/code extractors.
- **8 GB VRAM Friendly:** Single sequential job queue ensures heavy models never run concurrently. Ollama `keep_alive` unloads models when idle.
- **Dual Interfaces:** Rich CLI (`hushfile start`, `scan`, `undo`, `info`, `find`, `recent`, `doctor`, `bench`) and a clean Streamlit Web Dashboard.
- **Model Benchmark Suite:** Labeled test set with automated evaluations of category accuracy, latency, review share, and GPU VRAM usage.

---

## Architecture

```text
~/Downloads --> Watcher (watchdog) --> Stabilizer (size stability) --> Fingerprint (sha256 dedupe)
                                                                            |
                                                                            v
                                                                 Extractor (PDF/Office/Image/Audio/Text)
                                                                            |
                                                                            v
                                                              Classifier (Ollama, JSON Schema)
                                                                            |
                                                                            v
                                                       Decision Engine (confidence, naming, collisions)
                                                                            |
                                             +------------------------------+------------------------------+
                                             v                                                             v
                             Journal (SQLite Write-Ahead)                                    Indexer (ChromaDB Vector Store)
                                             |                                                             |
                                             v                                                             v
                              Mover -> ~/Organized/<Category>/                               Search, Info Card, Digest, UI
```

---

## Installation & Setup

### Prerequisites

1. **Python 3.11+**
2. **Ollama**:
   - Download & install from [ollama.com](https://ollama.com).
   - Pull the recommended models:
     ```bash
     ollama pull qwen2.5:7b-instruct
     ollama pull nomic-embed-text
     ollama pull qwen2.5vl:3b
     ```
3. **Tesseract OCR (for scanned PDFs & images)**:
   - **Windows:** Download the installer from the [UB-Mannheim Tesseract Wiki](https://github.com/UB-Mannheim/tesseract/wiki) and ensure `tesseract.exe` is added to your PATH (default: `C:\Program Files\Tesseract-OCR`).
   - **Linux:** `sudo apt install tesseract-ocr`
   - **macOS:** `brew install tesseract`

---

### Installation Commands by Operating System

####  Windows (PowerShell or Command Prompt)
```powershell
# 1. Clone & enter repository
git clone https://github.com/AntKss07/HushFile.git
cd HushFile

# 2. Create virtual environment
python -m venv .venv

# 3. Activate virtual environment
.\.venv\Scripts\Activate.ps1
# (or in cmd.exe: .\.venv\Scripts\activate.bat)

# 4. Install HushFile with all dependencies
pip install -e ".[audio,ui,dev]"
```

####  Apple macOS (Terminal / zsh / bash)
*(Works on Apple Silicon M1/M2/M3/M4 and Intel Macs)*
```bash
# 1. Clone & enter repository
git clone https://github.com/AntKss07/HushFile.git
cd HushFile

# 2. Create virtual environment
python3 -m venv .venv

# 3. Activate virtual environment
source .venv/bin/activate

# 4. Install HushFile with all dependencies
pip install -e ".[audio,ui,dev]"
```

> **Note on Apple iOS (iPhone / iPad):**  
> iOS is a sandboxed mobile operating system that does not support running Python background daemons or local Ollama LLM servers directly on-device. However, when you run HushFile on your Mac or Windows PC, you can access the **HushFile Web Dashboard** (`hushfile ui`) from your iPhone or iPad browser over your local Wi-Fi network at `http://<YOUR_COMPUTER_IP>:8501`.

---

## Usage Guide

*Both `hushfile` and `butler` work as command entrypoints.*

### 1. Run System Diagnostics
Verify Ollama connectivity, model availability, GPU detection, Tesseract OCR, and filesystem write permissions:

```bash
hushfile doctor
```

### 2. Start Foreground Watcher
Watch `~/Downloads` continuously. Heavy models run sequentially through a single worker queue:

```bash
hushfile start

# Dry-run mode: logs planned categorizations without modifying files
hushfile start --dry-run
```

### 3. One-Time Folder Scan
Process existing files in the download folder:

```bash
# Scan and file
hushfile scan

# Preview moves in a formatted table without moving files
hushfile scan --dry-run
```

### 4. Crash-Safe Undo
Reverse file moves:

```bash
# Reverse the most recent batch
hushfile undo

# Reverse a specific batch ID or move ID
hushfile undo --batch 3
hushfile undo --id 12

# Force undo even if the destination file was edited
hushfile undo --force
```

### 5. Quick-Info Card
Inspect why HushFile made a specific filing decision:

```bash
hushfile info "2026-10-23_cloud-server-invoice.pdf"
# Or look up by original filename or file ID
hushfile info 1
```

### 6. Semantic Search
Find files by meaning rather than filename:

```bash
hushfile find "cloud server invoice from last quarter"
hushfile find "calculus homework" --type Education
```

### 7. Daily Activity Digest
Review what HushFile organized while you were away:

```bash
hushfile recent
```

### 8. Web Dashboard
Launch the interactive Streamlit dashboard:

```bash
hushfile ui
# Navigate to http://localhost:8501
```

---

## Benchmarks & Evaluation

Run the automated model benchmark on the labeled synthetic test dataset:

```bash
# Evaluate models on GPU
hushfile bench --models "qwen2.5:7b-instruct,llama3.2:3b"

# CPU-only evaluation
hushfile bench --models "qwen2.5:7b-instruct" --cpu
```

### Labeled Test Results Summary

| Model | Device | Category Accuracy | Mean Time / File | Share to `_Review/` | Peak VRAM |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen3:4b` | CUDA | **100.0%** | 45.2s | 15.0% | **3741 MB** |
| `rule-fallback` | CPU | **60.0%** | 3.7s | 80.0% | 0 MB |

---

## Testing

HushFile includes a comprehensive test suite (32 tests) covering crash reconciliation, dry-run refusals, occupied-source file restorations, prompt injection defenses, multi-format extraction, and end-to-end lifecycles.

```bash
pytest -v
```

To run code quality and linter checks:

```bash
ruff check src tests bench
```

---

## Assumptions & Design Decisions

1. **Untrusted Document Text:** All extracted document content is wrapped in `<CONTENT>` tags and explicitly marked as untrusted data in system prompts to defend against prompt injection attacks.
2. **Confidence Auto-Normalization:** LLMs occasionally output integer percentages (e.g. `95` or `100`) instead of float values (`0.95`). Pydantic validators automatically detect and normalize values > 1.0 into the `[0.0, 1.0]` range.
3. **8 GB VRAM Protection:** To prevent out-of-memory crashes on laptop GPUs (such as RTX 5060), HushFile uses a single-threaded sequential worker queue. Heavy models are called strictly one at a time, and Ollama `keep_alive` unloads idle models.
4. **Safety & Zero Data Loss:** HushFile **never** deletes files. Operations consist only of safe filesystem moves and renames. If a restored file's original path is occupied, it is restored as `name (restored).ext` to avoid overwriting newer files.
5. **Cross-Platform Filename Sanitization:** Enforces maximum 80 characters, kebab-case, ISO date prefixes, and strips Windows reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`) and illegal filesystem characters (`<>:"/\|?*`).
