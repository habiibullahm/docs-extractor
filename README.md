# docex

**Automated extraction and validation of Indonesian financial documents**

docex transforms Indonesian tax invoices (e-Faktur/Coretax), supplier invoices, and receipts from PDF, scans, or phone photos into validated, structured JSON. It performs arithmetic checks (line items, DPP, PPN calculations), validates tax identifiers (NPWP/NIK), and routes low-confidence documents to a human review queue—ensuring only verified data flows through. Export approved documents to Excel or CSV for immediate use in ERP systems or tax filing.

---

## 🎯 Key Features

- **Multi-format document processing**: PDF, PNG, JPG, WEBP up to 10 MB
- **Three extraction approaches**: OCR + rules (free, offline), OCR + LLM, Vision LLM (best accuracy)
- **Comprehensive validation**: Arithmetic checks, PPN tax calculations, NPWP/NIK format validation, date consistency
- **Smart routing**: Auto-approve high-confidence documents, flag uncertain ones for human review
- **Human-in-the-loop review UI**: Side-by-side scan and fields, keyboard shortcuts, correction tracking
- **Export capabilities**: Excel workbook or CSV files with separate tables for documents and line items
- **Measured and reproducible**: 115-document evaluation suite with locked test split
- **Privacy-first public demo**: Per-visitor isolation, automatic cleanup, no login required

## 📊 Performance Benchmark

Evaluated on 115 synthetic Indonesian financial documents (faktur pajak, invoices, receipts) with varying image quality:

| Approach | Document Accuracy | Field Accuracy | Line Items F1 | Cost/1k docs | Latency (p50) |
|----------|-------------------|----------------|---------------|--------------|---------------|
| **A · OCR + Rules** | 71.3% | 90.5% | 0.856 | $0.03 | 2.5s |
| **B · OCR + LLM** | 79.1% | 95.4% | 0.978 | $2.45 | 7.7s |
| **C · Vision LLM** | **94.8%** | **98.2%** | 0.968 | $3.69 | 8.3s |

**Document accuracy** = all critical fields correct. **Field accuracy** = individual field correctness.

### Performance by Image Quality (Approach A)

| Quality | Clean | Light Noise | Medium Noise | Heavy Noise |
|---------|-------|-------------|--------------|-------------|
| **Accuracy** | 95.5% | 77.4% | 64.5% | 54.8% |

Vision LLM (Approach C) maintains 90.3% accuracy even on heavily degraded scans, compared to 54.8% for OCR-based extraction.

> **Note**: Full evaluation methodology and per-document-type breakdowns are in [`docs/technical.md`](docs/technical.md).

## 🏗️ Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        UI[Review Desk UI<br/>HTML/CSS/JS]
        API_CLIENT[API Client<br/>cURL/Python/etc]
    end

    subgraph "API Layer"
        API[FastAPI Server<br/>:8000]
    end

    subgraph "Queue & Worker"
        QUEUE[(Redis Queue<br/>RQ/in-process)]
        WORKER[RQ Worker]
    end

    subgraph "Document Pipeline"
        PREPROCESS[Preprocess<br/>PDF→Image, Deskew<br/>Orientation]
        CLASSIFY[Classify<br/>Faktur/Invoice/Receipt]
        
        subgraph "Extraction Methods"
            METHOD_A[A: Tesseract OCR<br/>+ Regex Rules]
            METHOD_B[B: OCR Text<br/>+ LLM Schema Fill]
            METHOD_C[C: Vision LLM<br/>Direct Image Read]
        end
        
        NORMALIZE[Normalize<br/>Dates, Amounts, IDs]
        VALIDATE[Validate<br/>Arithmetic, Tax Rules<br/>NPWP/NIK Format]
        REPAIR[Repair Pass<br/>LLM Fix Inconsistencies]
        SCORE[Score & Route<br/>Min Confidence Check]
    end

    subgraph "Storage"
        DB[(PostgreSQL<br/>Results + Metadata)]
    end

    subgraph "Decision Routes"
        AUTO[Auto-Approved<br/>High Confidence]
        REVIEW[Review Queue<br/>Low Confidence/Failed Validation]
    end

    UI --> API
    API_CLIENT --> API
    API --> QUEUE
    QUEUE --> WORKER
    
    WORKER --> PREPROCESS
    PREPROCESS --> CLASSIFY
    CLASSIFY --> METHOD_A
    CLASSIFY --> METHOD_B
    CLASSIFY --> METHOD_C
    
    METHOD_A --> NORMALIZE
    METHOD_B --> NORMALIZE
    METHOD_C --> NORMALIZE
    
    NORMALIZE --> VALIDATE
    VALIDATE -->|Errors + B/C only| REPAIR
    REPAIR --> NORMALIZE
    VALIDATE --> SCORE
    
    SCORE -->|Score ≥ Threshold| AUTO
    SCORE -->|Below Threshold| REVIEW
    
    AUTO --> DB
    REVIEW --> DB
    DB --> UI
    
    style METHOD_C fill:#90EE90
    style AUTO fill:#D3D3D3
    style REVIEW fill:#FFB366
```

### Pipeline Stages

1. **Preprocess**: Convert PDF to image, detect orientation, deskew, normalize dimensions
2. **Classify**: Identify document type (faktur pajak, invoice, receipt) using layout patterns
3. **Extract**: Apply one of three methods (A/B/C) to read structured fields
4. **Normalize**: Convert Indonesian formats to standard types (dates, amounts, NPWP)
5. **Validate**: Check arithmetic (line totals, PPN = DPP × rate), format rules, date logic
6. **Repair** (B/C only): Single LLM pass to fix validation errors; marks document as inconsistent if unfixable
7. **Score**: Minimum confidence of critical fields × validation penalties
8. **Route**: Auto-approve if score ≥ threshold; otherwise send to review queue

## 🛠️ Tech Stack

**Backend**
- Python 3.11+ with Pydantic v2 schemas
- FastAPI for REST API and UI serving
- RQ (Redis Queue) for asynchronous job processing
- PostgreSQL for persistent storage (SQLite supported for dev)
- Redis for queue and caching

**Document Processing**
- Tesseract OCR (with Indonesian language pack) + pytesseract
- PyMuPDF (fitz) for PDF rendering
- OpenCV + PIL for image preprocessing
- RapidOCR (optional, PaddleOCR ONNX models)

**LLM/VLM Integration**
- Gemini API (free tier, default)
- OpenAI GPT-4 models
- Anthropic Claude
- Any OpenAI-compatible local server (Ollama/vLLM)

**Frontend**
- Vanilla HTML/CSS/JavaScript (no build step)
- IBM Plex Sans + Mono typefaces
- Responsive design with keyboard shortcuts

**Evaluation & Testing**
- pytest for unit and integration tests
- Custom eval harness with strict field/document accuracy metrics
- Matplotlib + pandas for benchmark visualization

**DevOps**
- Docker + Docker Compose
- GitHub Actions CI with eval regression checks
- Optional deployment to Hugging Face Spaces

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

Run the full stack (API, worker, PostgreSQL, Redis) with one command:

```bash
# Clone and navigate
git clone https://github.com/yourusername/docex.git
cd docex

# Optional: Enable LLM approaches B and C
cp .env.example .env
# Edit .env: Add GEMINI_API_KEY and set DOCEX_ENABLE_LLM=true

# Start all services
docker compose up --build

# Access the application
# Review UI:  http://localhost:8000
# API docs:   http://localhost:8000/docs
```

The Docker setup includes health checks and automatically waits for PostgreSQL/Redis to be ready.

### Option 2: Local Development

```bash
# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[api,eval,synth,dev]"
playwright install chromium

# Install Tesseract OCR
# Ubuntu/Debian:
sudo apt-get install tesseract-ocr tesseract-ocr-ind

# macOS:
brew install tesseract tesseract-lang

# Generate synthetic dataset (600 documents)
python -m synth.build --per-type 200

# Run API server
uvicorn docex.api.app:app --port 8000 --reload

# In another terminal: Run worker (if using Redis queue)
rq worker docex --url redis://localhost:6379/0
```

### Using the API

```bash
# Upload a document
curl -c cookies.txt -b cookies.txt \
  -F "file=@invoice.pdf" \
  http://localhost:8000/documents
# Response: {"job_id": "abc123", "status": "pending"}

# Check result
curl -b cookies.txt http://localhost:8000/documents/abc123
# Returns: extracted fields, confidence scores, validation results, route decision

# Export approved documents
curl -b cookies.txt http://localhost:8000/export/approved?format=xlsx -o approved.xlsx
curl -b cookies.txt http://localhost:8000/export/approved?format=csv&part=documents -o docs.csv
```

**Note**: The session cookie (`docex_sid`) is required—each visitor has isolated data.

## 🧪 Running Tests and Evaluations

### Unit and Integration Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=docex --cov-report=html

# Specific test file
pytest tests/test_pipeline.py -v

# Fast smoke test (no slow integration tests)
pytest -m "not slow"
```

### Evaluation Suite

```bash
# Evaluate approach A (OCR + rules) on dev split
python -m eval.run --approach A --split dev

# Evaluate all approaches (requires DOCEX_ENABLE_LLM=true)
python -m eval.run --approach A B C --split dev

# Evaluate on locked test split (use --final flag)
python -m eval.run --approach A --split test --final

# Generate comparison report
python -m eval.summarize

# View reports
cat eval/reports/A_dev_ci/report.md
cat eval/reports/comparison_dev.md
```

**Evaluation outputs**:
- `eval/reports/<approach>_<split>/report.md`: Per-field accuracy, per-document-type breakdown, threshold curves
- `eval/reports/<approach>_<split>/errors.md`: 20 worst-performing documents with images and error analysis
- `eval/reports/<approach>_<split>/threshold_curve.png`: Straight-through rate vs. error rate visualization
- `eval/reports/comparison_<split>.md`: Side-by-side comparison table

### CI Regression Tests

The GitHub Actions workflow (`ci.yml`) runs:
- All unit tests
- **Eval regression check** on 36-document frozen subset: fails if accuracy drops >2 points from baseline
- Docker build + smoke test
- Optional CORD (real receipt) evaluation

## 🎨 Design Decisions & Tradeoffs

### 1. Three Approaches, One Pipeline
All three extraction methods (OCR+rules, OCR+LLM, Vision LLM) share the same preprocessing, validation, and scoring logic. This ensures benchmark comparisons isolate the extractor's performance. **Tradeoff**: More code to maintain three extractors vs. single flexible approach, but enables accurate cost/accuracy analysis.

### 2. Confidence Calibration Over Blind Trust
Score is calculated as `min(critical_field_confidence, classifier_confidence) × validation_penalties`. A document is only as trustworthy as its weakest critical field. **Tradeoff**: Vision LLM (94.8% doc accuracy) routes everything to review because its confidence scores don't yet predict errors—human still checks every doc despite high accuracy.

### 3. Models Never "Fix" Numbers
The extraction prompt forbids computing missing values. Validation errors trigger a repair pass that must preserve printed values or flag the document as inconsistent. **Tradeoff**: ~4% of synthetic docs are deliberately inconsistent to penalize models that quietly "repair" totals—this prevents the most dangerous failure mode in finance automation.

### 4. Tax Rules as Configuration
NPWP formats (15/16 digit), faktur serial patterns (16/17 digit e-Faktur/Coretax), PPN rates (11%/12%), and luxury tax rules live in `configs/tax_rules.yaml`. **Tradeoff**: Easier to update for regulatory changes without code changes, but rules need verification against DJP sources (some marked `TODO(verify)`).

### 5. Synthetic Data First, Real Data Later
The entire dataset (600+ docs) is synthetic, generated from HTML templates with controlled degradations (blur, rotation, lighting). **Tradeoff**: Faster iteration and no privacy concerns, but real-world edge cases (handwritten notes, crumpled receipts, non-template layouts) may underperform. CORD (real receipts) integration exists but not fully evaluated.

### 6. Per-Visitor Isolation in Public Demo
Each browser gets a random session cookie; all data (uploads, results, queue) is scoped to that session and auto-deleted after 2 hours. **Tradeoff**: No login/persistence means visitors can't resume work later, but zero-friction demo for recruiters and hiring managers.

### 7. Free Offline Fallback
Approach A (Tesseract + regex) costs ~$0.03/1k docs and runs entirely offline. Vision LLM costs $3.69/1k but achieves 94.8% accuracy. **Tradeoff**: A is the default to keep costs predictable; B/C require explicit `DOCEX_ENABLE_LLM=true` to prevent accidental API spend.

### 8. Locked Test Split
The test set (`data/test_lock.json`) is never used for tuning; `eval/run.py` requires `--final` flag to run it. All OCR settings and heuristics were tuned on train/dev only. **Tradeoff**: Prevents overfitting but means final test numbers only emerge after development is complete.

## 📸 Demo & Screenshots

**Temporary live demo**: [http://43.157.227.176:7860](http://43.157.227.176:7860) (interface in Indonesian, uses public IP over HTTP—not production-ready URL)

> **⚠️ Demo Notice**: This is a public demo with synthetic data. Do not upload documents containing real NPWP, NIK, or personal information. All uploads are deleted after 2 hours.

### Review Workspace
![Review workspace: scan on the left, extracted fields with confidence scores on the right](docs/screenshots/workspace.png)

### Review Queue
![Review queue sorted by confidence score, showing documents that need human verification](docs/screenshots/queue.png)

### Try a Document
![Sample gallery and upload interface with method picker](docs/screenshots/try.png)

## 📚 Documentation

| File | Description |
|------|-------------|
| [`docs/technical.md`](docs/technical.md) | Architecture deep-dive, full evaluation results, cost analysis, technical decisions, limitations |
| [`docs/operations.md`](docs/operations.md) | API reference, CI setup, cost control, deployment to VPS/Hugging Face, repository layout |
| [`design.md`](design.md) | UI/UX design specification for the review desk interface |
| [`data/README.md`](data/README.md) | Synthetic dataset generation and test split locking mechanism |

## 🔧 Configuration

Key environment variables (create `.env` from `.env.example`):

```bash
# Database (defaults to SQLite in dev)
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/docex

# Queue backend
REDIS_URL=redis://localhost:6379/0
QUEUE_BACKEND=rq  # or "sync" for synchronous processing

# LLM approaches (B and C)
DOCEX_ENABLE_LLM=false           # Must be true to use LLM approaches
GEMINI_API_KEY=your_key_here      # Google AI Studio (free tier)
# OPENAI_API_KEY=your_key_here    # Alternative
# ANTHROPIC_API_KEY=your_key_here # Alternative

# Storage
DOCEX_STORAGE_DIR=/app/data/uploads

# Public demo limits (see docs/operations.md)
DOCEX_LIMITS__RETENTION_MINUTES=120
DOCEX_LIMITS__SESSION_MAX_DOCS=20
DOCEX_LIMITS__LLM_DAILY_DOCS=20
```

## 🤝 Use Cases

### Finance and Tax Teams
- **AP/AR Processing**: Digitize supplier invoices and customer receipts automatically
- **PPN Input Credit**: Extract e-Faktur data for tax credit claims with validation
- **Bookkeeping Services**: Process client receipts in bulk with human-in-the-loop for uncertain docs
- **ERP Integration**: Export validated data to Excel/CSV for import into accounting systems

### Engineers and Hiring Managers
- **Portfolio Review**: Explore the live demo, review code structure, and read measured benchmarks
- **Technical Assessment**: Examine evaluation methodology, reproducibility, and engineering decisions
- **Architecture Study**: Understand multi-model pipeline design with shared validation and routing

## 📜 License

Every document in this repository is synthetic or public domain. No real NPWP or NIK is used anywhere.

## 👤 About

Built by [Muhammad](https://github.com/yourusername), a backend/full-stack developer specializing in API design, data pipelines, and AI-powered automation. This project demonstrates:

- End-to-end API development with FastAPI and async workers
- Multi-model ML pipeline architecture with systematic evaluation
- Production-ready error handling, validation, and observability
- Privacy-first public demo deployment with rate limiting and isolation
- Comprehensive documentation and reproducible benchmarks

**Looking for**: Remote full-stack or AI engineering roles. Open to discussing document AI, LLM integration, API design, or financial automation projects.

---

**Repository size**: ~56 MB (includes 16 MB of evaluation reports and CI test data)

**Questions?** Open an issue or check [`docs/technical.md`](docs/technical.md) for detailed architecture notes.
