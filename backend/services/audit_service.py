"""Audit Trail Service"""

import uuid
from datetime import datetime
from typing import List, Optional

_audit_log = []


def log_action(action: str, entity: str, amount: float, status: str, session_id: str):
    entry = {
        "id": f"aud_{uuid.uuid4().hex[:8]}",
        "action": action,
        "entity": entity,
        "amount": amount,
        "status": status,
        "session_id": session_id,
        "timestamp": datetime.now().isoformat()
    }
    _audit_log.append(entry)
    return entry


def get_audit_trail(session_id: Optional[str] = None) -> List[dict]:
    if session_id:
        return [e for e in _audit_log if e["session_id"] == session_id]
    return _audit_log
