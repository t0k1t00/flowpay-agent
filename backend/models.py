"""Pydantic models for PayGentic API"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SourcingRequest(BaseModel):
    query: str = Field(..., description="Natural language sourcing request")
    session_id: Optional[str] = None


class ParsedRequest(BaseModel):
    material: str
    quantity_kg: float
    max_budget_per_kg: float
    delivery_days: int
    location: Optional[str] = None
    raw_query: str


class Supplier(BaseModel):
    id: str
    company_name: str
    price_per_kg: float
    delivery_days: int
    verified: bool
    gstin: str
    email: str
    phone: Optional[str] = None
    location: str
    website: Optional[str] = None
    score: int
    category: str = "General"
    recommended: bool = False


class SupplierResponse(BaseModel):
    suppliers: List[Supplier]
    total: int
    ranked: bool = True


class EscrowRecord(BaseModel):
    id: str
    supplier: str
    amount: float
    status: str  # pending | locked | approved | released | refunded | rejected
    created_at: str
    session_id: str
    requires_approval: bool = False


class EscrowRequest(BaseModel):
    supplier: str
    amount: float
    session_id: str = "manual"


class ApprovalRequest(BaseModel):
    escrow_id: str
    session_id: str = "manual"
    reason: Optional[str] = None


class EmailRequest(BaseModel):
    supplier_id: str
    material: str
    quantity: str
    max_budget: str
    delivery_days: int
    session_id: Optional[str] = None


class AuditEntry(BaseModel):
    id: str
    action: str
    entity: str
    amount: float
    status: str
    session_id: str
    timestamp: str


class WalletState(BaseModel):
    balance: float = 50000.0
    spent: float = 0.0
    limit: float = 30000.0
    escrow_locked: float = 0.0
