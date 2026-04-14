# PayGentic

Autonomous B2B sourcing and escrow workflow built for the Locus Paygentic Hackathon (Week 1: PayWithLocus suite).

## Week 1 Alignment

This implementation is aligned to the Week 1 track because it focuses on:

- Agent-initiated supplier discovery (Exa-style flow)
- Agent-initiated web data extraction (Firecrawl-style flow)
- PayWithLocus-style spending controls and budget enforcement
- Human-in-the-loop approval threshold for high-value payments
- Escrow lifecycle (reserve, approve/reject, release/refund)
- Real-time agent reasoning and financial event streaming

## K Scope Status

Implemented in this repo:

- Agent orchestration framework with tool interfaces (`backend/services/orchestrator.py`)
- Locus wallet + spending controls (`backend/services/spending_controls.py`)
- Exa + Firecrawl integrated search pipeline with micropayment charging and fallback mode (`backend/services/supplier_search.py`)
- HITL spending threshold and approval events (`backend/services/orchestrator.py`, `backend/main.py`)
- Escrow lock/release/refund logic backed by wallet state transitions (`backend/services/escrow_service.py`)
- Real-time agent log panel connected to backend WebSocket stream (`frontend/index.html`)
- Backend APIs for wallet state and spending control configuration (`backend/main.py`)

Out of code scope:

- Demo video recording (manual task)
- External account funding (Locus dashboard action)

## Architecture

```
Frontend Dashboard (index.html)
  - Start sourcing session
  - WebSocket log stream
  - Supplier cards
  - Approval modal
  - Wallet + escrow timeline

FastAPI Backend (main.py)
  - /api/request-source
  - /ws/logs
  - /api/wallet-state
  - /api/spending-controls
  - escrow + audit endpoints

Orchestrator Tool Chain
  1) Parse intent
  2) Search suppliers (Exa path)
  3) Scrape/enrich (Firecrawl + Apollo path)
  4) Rank suppliers
  5) Send quote emails
  6) Create escrow + trigger approval when needed

Control Layer
  - Monthly/daily limits
  - Auto-approve threshold
  - Allowed vendor categories
  - Wallet ledger for micropayments and escrow events
```

## Project Structure

```
backend/
  main.py
  models.py
  requirements.txt
  .env.example
  services/
    orchestrator.py
    spending_controls.py
    supplier_search.py
    escrow_service.py
    email_service.py
    parser.py
    audit_service.py
  websocket/
    manager.py
  database/
    db.py

frontend/
  index.html
  suppliers.html
  audit.html
  approvals.html
  settings.html
```

## Run Locally

### 1) Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Backend runs on `http://localhost:8000`
WebSocket stream is at `ws://localhost:8000/ws/logs`

### 2) Frontend

Open `frontend/index.html` in browser, or serve statically:

```bash
cd frontend
npx serve .
```

## Environment Variables

Core variables in `backend/.env.example`:

- `EXA_API_KEY`
- `FIRECRAWL_API_KEY`
- `RESEND_API_KEY`
- `LOCUS_API_KEY`
- `LOCUS_API_BASE`
- `AUTO_APPROVE_THRESHOLD`
- `MONTHLY_SPEND_LIMIT`
- `DAILY_SPEND_LIMIT`
- `WALLET_BALANCE`
- `ALLOWED_VENDOR_CATEGORIES`
- `USE_LIVE_APIS`
- `USE_LOCUS_WRAPPED_APIS`

`USE_LIVE_APIS=false` (default) uses deterministic mock fallback with real control logic.

## API Endpoints

Main workflow:

- `POST /api/request-source`
- `GET /api/session/{session_id}`
- `WS /ws/logs`

Wallet and controls:

- `GET /api/wallet-state`
- `POST /api/wallet-topup`
- `GET /api/wallet-ledger`
- `GET /api/spending-controls`
- `PUT /api/spending-controls`

Escrow and approvals:

- `POST /api/create-escrow`
- `GET /api/escrows`
- `GET /api/approvals`
- `POST /api/approve-payment`
- `POST /api/reject-payment`
- `POST /api/release-escrow`
- `POST /api/refund-escrow`

Audit:

- `GET /api/audit-trail`

## Notes

- Current wallet, audit, and escrow stores are in-memory services for hackathon speed.
- SQLite initialization is present and can be used for persistence extension.
- The dashboard now consumes live backend events instead of synthetic client-only timeline data.
