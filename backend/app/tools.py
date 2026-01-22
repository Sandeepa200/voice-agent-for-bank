from __future__ import annotations

import time
from typing import List, Dict, Optional

from langchain_core.tools import tool

VERIFICATION_TTL_SECONDS = 10 * 60
_VERIFIED_UNTIL: Dict[str, float] = {}


def reset_verification(customer_id: str) -> None:
    _VERIFIED_UNTIL.pop(customer_id, None)


def _is_verified(customer_id: str) -> bool:
    until = _VERIFIED_UNTIL.get(customer_id)
    if not until:
        return False
    if time.time() > until:
        _VERIFIED_UNTIL.pop(customer_id, None)
        return False
    return True


MOCK_DB: Dict[str, Dict] = {
    "customers": {
        "user_123": {
            "pin": "1234",
            "name": "John Doe",
            "profile": {
                "address": "12 Main St, Springfield, IL 62701",
                "phone": "+1-202-555-0100",
                "email": "john.doe@example.com",
            },
            "accounts": [
                {"account_id": "acc_123", "type": "checking", "currency": "USD", "available": 5000.00}
            ],
            "cards": [
                {"card_id": "card_123", "status": "active", "last4": "4242", "network": "VISA"}
            ],
            "transactions": [
                {"id": "tx_1", "amount": -50.00, "merchant": "Walmart", "status": "completed", "ts": "2026-01-20T12:01:00Z"},
                {"id": "tx_2", "amount": -12.00, "merchant": "Netflix", "status": "completed", "ts": "2026-01-19T08:30:00Z"},
                {"id": "tx_3", "amount": -100.00, "merchant": "Unknown", "status": "declined", "ts": "2026-01-18T15:12:00Z"},
            ],
            "statements": [
                {"period": "2025-12", "statement_id": "st_202512", "format": "pdf"},
                {"period": "2025-11", "statement_id": "st_202511", "format": "pdf"},
            ],
        }
    },
    "cards": {
        "card_123": {"customer_id": "user_123", "status": "active"}
    },
    "disputes": {},
}


@tool
def verify_identity(customer_id: str, pin: str) -> bool:
    """Verify a customer's identity using customer_id and PIN."""
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return False
    if customer["pin"] != pin:
        return False
    _VERIFIED_UNTIL[customer_id] = time.time() + VERIFICATION_TTL_SECONDS
    return True


@tool
def get_account_balance(customer_id: str) -> Dict:
    """Return the customer's account balance details (requires verification)."""
    if not _is_verified(customer_id):
        return {"error": "identity_not_verified"}
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return {"error": "customer_not_found"}
    acct = customer["accounts"][0]
    return {
        "customer_id": customer_id,
        "account_id": acct["account_id"],
        "type": acct["type"],
        "available": acct["available"],
        "currency": acct["currency"],
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@tool
def get_recent_transactions(customer_id: str, count: int = 3) -> List[Dict]:
    """Return the customer's most recent transactions (requires verification)."""
    if not _is_verified(customer_id):
        return [{"error": "identity_not_verified"}]
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return [{"error": "customer_not_found"}]
    safe_count = max(1, min(int(count), 20))
    return customer["transactions"][:safe_count]


@tool
def block_card(card_id: str, reason: str) -> str:
    """Block a card permanently by card_id (requires verification)."""
    card = MOCK_DB["cards"].get(card_id)
    if not card:
        return "Error: Card not found."
    customer_id = card["customer_id"]
    if not _is_verified(customer_id):
        return "Error: Identity not verified."
    card["status"] = "blocked"
    customer = MOCK_DB["customers"].get(customer_id)
    if customer:
        for c in customer["cards"]:
            if c["card_id"] == card_id:
                c["status"] = "blocked"
                break
    return f"Card {card_id} has been blocked. Reason: {reason}"


@tool
def get_customer_cards(customer_id: str) -> List[Dict]:
    """List a customer's cards (requires verification)."""
    if not _is_verified(customer_id):
        return [{"error": "identity_not_verified"}]
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return [{"error": "customer_not_found"}]
    return customer["cards"]


@tool
def request_statement(customer_id: str, period: str) -> Dict:
    """Request a monthly statement for a given period (YYYY-MM) (requires verification)."""
    if not _is_verified(customer_id):
        return {"error": "identity_not_verified"}
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return {"error": "customer_not_found"}
    for s in customer["statements"]:
        if s["period"] == period:
            return {"statement_id": s["statement_id"], "period": period, "format": s["format"], "status": "ready"}
    return {"error": "statement_not_found", "available_periods": [s["period"] for s in customer["statements"]]}


@tool
def update_address(customer_id: str, new_address: str) -> Dict:
    """Update the customer's profile address (requires verification)."""
    if not _is_verified(customer_id):
        return {"error": "identity_not_verified"}
    customer = MOCK_DB["customers"].get(customer_id)
    if not customer:
        return {"error": "customer_not_found"}
    customer["profile"]["address"] = new_address.strip()
    return {"status": "updated", "address": customer["profile"]["address"]}


@tool
def report_cash_not_dispensed(customer_id: str, atm_id: str, amount: float, date: str) -> Dict:
    """Submit a dispute for an ATM cash-not-dispensed incident (requires verification)."""
    if not _is_verified(customer_id):
        return {"error": "identity_not_verified"}
    if customer_id not in MOCK_DB["customers"]:
        return {"error": "customer_not_found"}
    dispute_id = f"disp_{int(time.time())}"
    MOCK_DB["disputes"][dispute_id] = {
        "customer_id": customer_id,
        "type": "cash_not_dispensed",
        "atm_id": atm_id,
        "amount": amount,
        "date": date,
        "status": "submitted",
    }
    return {"dispute_id": dispute_id, "status": "submitted"}
