# PayGentic — Autonomous B2B Sourcing & Escrow Agent

**Locus Paygentic Hackathon 2026**

An AI agent that autonomously handles the full B2B procurement lifecycle — from natural language request to escrow payment with human-in-the-loop approval.

---

## Architecture

```
User Request
    │
    ▼
Request Parser (LangChain / Pydantic)
    │
    ├──▶ Supplier Search (Exa API)
    ├──▶ Web Scraping (Firecrawl)
    └──▶ Data Enrichment (Apollo / GSTIN)
         │
         ▼
    Supplier Ranking Engine
         │
         ▼
    Email Automation (Resend)
         │
         ▼
    Escrow Creation (Locus)
         │
         ├── Under ₹2,000 ──▶ Auto-approve ──▶ Lock
         └── Over ₹2,000  ──▶ Human Approval Center
                                    │
                              Approve / Reject
                                    │
                              Shipment Proof Upload
                                    │
                          Release Escrow / Refund
         │
         ▼
    Real-time WebSocket Audit Trail
```

---

## Quick Start

### Backend (FastAPI)

```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # Fill in your API keys
python main.py
# Server runs at http://localhost:8000
# WebSocket at ws://localhost:8000/ws/logs
```

### Frontend (Static HTML)

Open `frontend/index.html` directly in your browser, or:

```bash
cd frontend
npx serve .
# Serves at http://localhost:3000
```

---

## Pages

| Page | File | Description |
|------|------|-------------|
| Dashboard | `index.html` | Main split-screen agent interface |
| Suppliers | `suppliers.html` | Full supplier directory with filters |
| Audit Trail | `audit.html` | Tamper-proof transaction log |
| Approvals | `approvals.html` | Human approval center |
| Settings | `settings.html` | API keys & spending limits |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/request-source` | Start agent pipeline |
| GET | `/api/suppliers` | List suppliers |
| POST | `/api/send-email` | Send quote email |
| POST | `/api/create-escrow` | Create escrow payment |
| POST | `/api/approve-payment` | Approve transaction |
| POST | `/api/reject-payment` | Reject transaction |
| POST | `/api/release-escrow` | Release funds to supplier |
| POST | `/api/refund-escrow` | Refund funds to wallet |
| GET | `/api/audit-trail` | Get audit log |
| WS | `/ws/logs` | Real-time log stream |

---

## Demo Script (Hackathon Presentation)

### Setup (before demo)
- Open `index.html` in browser (full screen)
- Have the backend running (`python main.py`)

### Step-by-step Demo (8 minutes)

**1. Show the dashboard (30s)**
> "This is PayGentic — an autonomous B2B procurement agent. The left panel shows agent reasoning in real-time. The center shows supplier results. The right is the financial audit trail."

**2. Enter a sourcing request (30s)**
> Type: `Find 1000kg cotton yarn suppliers under ₹300/kg delivered within 5 days in Tamil Nadu`
> Click **Start Agent**

**3. Watch the agent work (2 min)**
> Narrate each log as it streams:
> - "The agent is parsing the natural language request..."
> - "Now searching suppliers via Exa API..."
> - "Scraping supplier websites with Firecrawl..."
> - "Enriching data — GSTIN validation, company scores..."
> - "Ranking suppliers by price, delivery time, and verification..."

**4. Show supplier cards (1 min)**
> "8 suppliers ranked. TamilTex Mills is recommended — Score 92, ₹280/kg, 4-day delivery."
> Click **View Details** on TamilTex Mills
> Show GSTIN, contact, pricing breakdown

**5. Email automation (30s)**
> "The agent automatically dispatched 8 quote request emails via Resend API."
> Click **Send Quote** to demonstrate

**6. Escrow creation + approval (2 min)**
> "The agent selected TamilTex Mills and created a ₹28,000 Locus escrow payment."
> Show the approval modal: "Amount exceeds our ₹2,000 threshold — agent pauses for human approval."
> Click **Approve** → show escrow status update to 'Approved'

**7. Audit trail (1 min)**
> Navigate to Audit Trail page
> "Every action is logged — search, email, escrow, approval. Export to CSV for compliance."

**8. Release escrow (30s)**
> Back on dashboard: "Upload shipment proof, then release escrow to pay the supplier."
> Click **Release** → show 'Released' status

---

## Simulated vs Real APIs

| Feature | Demo Mode | Production |
|---------|-----------|------------|
| Supplier Search | Mock data (8 suppliers) | Exa API |
| Web Scraping | Pre-enriched data | Firecrawl |
| Company Enrichment | Static scores | Apollo API |
| Email Sending | Console log | Resend API |
| Escrow Payment | In-memory state | Locus Paygentic API |
| GSTIN Validation | Mock valid | GST Portal API |

---

## Project Structure

```
paygentic/
├── frontend/
│   ├── index.html          Dashboard
│   ├── suppliers.html      Supplier directory
│   ├── audit.html          Audit trail
│   ├── approvals.html      Approval center
│   └── settings.html       Settings
│
├── backend/
│   ├── main.py             FastAPI app + agent orchestration
│   ├── models.py           Pydantic data models
│   ├── requirements.txt    Dependencies
│   ├── .env.example        Environment variables
│   ├── services/
│   │   ├── parser.py           Request parsing
│   │   ├── supplier_search.py  Search + enrich + rank
│   │   ├── email_service.py    Email automation
│   │   ├── escrow_service.py   Escrow management
│   │   └── audit_service.py    Audit logging
│   ├── database/
│   │   └── db.py           SQLite schema + init
│   └── websocket/
│       └── manager.py      WebSocket broadcast manager
│
└── README.md
```

---

## Built With

- **FastAPI** — Python async backend
- **WebSockets** — Real-time log streaming
- **SQLite** — Local audit storage
- **Syne + IBM Plex Mono** — Typography
- **Vanilla HTML/CSS/JS** — Zero-dependency frontend

## Team
Locus Paygentic Hackathon 2026
