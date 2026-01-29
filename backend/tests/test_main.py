"""
Comprehensive Test Suite for Bank ABC Voice Agent

Tests cover:
1. Guardrails - Identity verification enforcement
2. Tool execution - All mock banking tools
3. Orchestration - Flow routing
4. API endpoints - FastAPI routes
"""

import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools import (
    verify_identity_raw,
    get_account_balance,
    get_recent_transactions,
    get_customer_profile,
    get_customer_cards,
    block_card,
    request_statement,
    update_address,
    report_cash_not_dispensed,
    get_verification_status,
    reset_verification,
    set_verification_state,
    _normalize_customer_id,
    _normalize_pin,
    _is_verified,
    MOCK_DB,
)

# Try to import FastAPI test client - skip API tests if dependencies missing
try:
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    HAS_API_DEPS = True
except ImportError:
    HAS_API_DEPS = False
    client = None


# ============================================================
# FIXTURE: Reset verification state before each test
# ============================================================

@pytest.fixture(autouse=True)
def reset_state():
    """Reset verification state and card status before each test."""
    reset_verification("John123")
    # Reset card status to active
    MOCK_DB["cards"]["card_123"]["status"] = "active"
    for card in MOCK_DB["customers"]["John123"]["cards"]:
        if card["card_id"] == "card_123":
            card["status"] = "active"
    yield


# ============================================================
# 1. GUARDRAIL TESTS - Identity Verification Enforcement
# ============================================================

class TestGuardrails:
    """Test that identity verification is strictly enforced."""

    def test_balance_requires_verification(self):
        """Cannot get balance without verification."""
        result = get_account_balance.invoke({"customer_id": "John123"})
        assert result.get("error") == "identity_not_verified"

    def test_transactions_require_verification(self):
        """Cannot get transactions without verification."""
        result = get_recent_transactions.invoke({"customer_id": "John123", "count": 3})
        assert isinstance(result, list)
        assert result[0].get("error") == "identity_not_verified"

    def test_profile_requires_verification(self):
        """Cannot get profile without verification."""
        result = get_customer_profile.invoke({"customer_id": "John123"})
        assert result.get("error") == "identity_not_verified"

    def test_cards_require_verification(self):
        """Cannot get cards without verification."""
        result = get_customer_cards.invoke({"customer_id": "John123"})
        assert isinstance(result, list)
        assert result[0].get("error") == "identity_not_verified"

    def test_block_card_requires_verification(self):
        """Cannot block card without verification."""
        result = block_card.invoke({"card_id": "card_123", "reason": "lost"})
        assert "Identity not verified" in result

    def test_statement_requires_verification(self):
        """Cannot request statement without verification."""
        result = request_statement.invoke({"customer_id": "John123", "period": "2025-12"})
        assert result.get("error") == "identity_not_verified"

    def test_update_address_requires_verification(self):
        """Cannot update address without verification."""
        result = update_address.invoke({"customer_id": "John123", "new_address": "123 New St"})
        assert result.get("error") == "identity_not_verified"

    def test_dispute_requires_verification(self):
        """Cannot report dispute without verification."""
        result = report_cash_not_dispensed.invoke({
            "customer_id": "John123",
            "atm_id": "ATM001",
            "amount": 100.0,
            "date": "2026-01-20"
        })
        assert result.get("error") == "identity_not_verified"


# ============================================================
# 2. VERIFICATION TESTS
# ============================================================

class TestVerification:
    """Test identity verification logic."""

    def test_verify_with_correct_credentials(self):
        """Verification succeeds with correct customer_id and PIN."""
        result = verify_identity_raw("John123", "1234")
        assert result is True
        assert _is_verified("John123") is True

    def test_verify_with_wrong_pin(self):
        """Verification fails with wrong PIN."""
        result = verify_identity_raw("John123", "9999")
        assert result is False
        assert _is_verified("John123") is False

    def test_verify_with_unknown_customer(self):
        """Verification fails with unknown customer."""
        result = verify_identity_raw("Unknown999", "1234")
        assert result is False

    def test_verify_case_insensitive_customer_id(self):
        """Customer ID matching is case-insensitive."""
        result = verify_identity_raw("john123", "1234")
        assert result is True

    def test_verify_with_spaces_in_customer_id(self):
        """Customer ID normalization handles spaces."""
        result = verify_identity_raw("John 123", "1234")
        assert result is True

    def test_verify_with_spaces_in_pin(self):
        """PIN normalization handles spaces (voice transcription)."""
        result = verify_identity_raw("John123", "1 2 3 4")
        assert result is True

    def test_verify_with_commas_in_pin(self):
        """PIN normalization handles commas."""
        result = verify_identity_raw("John123", "1,2,3,4")
        assert result is True

    def test_verify_pin_too_short(self):
        """PIN must be at least 4 digits."""
        result = verify_identity_raw("John123", "123")
        assert result is False

    def test_verify_pin_too_long(self):
        """PIN must be at most 6 digits."""
        result = verify_identity_raw("John123", "1234567")
        assert result is False

    def test_verification_status_before_verify(self):
        """Verification status is false before verification."""
        result = get_verification_status.invoke({"customer_id": "John123"})
        assert result["verified"] is False

    def test_verification_status_after_verify(self):
        """Verification status is true after verification."""
        verify_identity_raw("John123", "1234")
        result = get_verification_status.invoke({"customer_id": "John123"})
        assert result["verified"] is True

    def test_reset_verification(self):
        """Reset verification clears verified state."""
        verify_identity_raw("John123", "1234")
        assert _is_verified("John123") is True
        reset_verification("John123")
        assert _is_verified("John123") is False

    def test_set_verification_state(self):
        """Can manually set verification state."""
        set_verification_state("John123", True)
        assert _is_verified("John123") is True
        set_verification_state("John123", False)
        assert _is_verified("John123") is False


# ============================================================
# 3. NORMALIZATION TESTS
# ============================================================

class TestNormalization:
    """Test input normalization for voice transcription variations."""

    def test_normalize_customer_id_with_spaces(self):
        """Spaces are removed from customer ID."""
        assert _normalize_customer_id("John 123") == "John123"

    def test_normalize_customer_id_with_commas(self):
        """Commas are removed from customer ID."""
        assert _normalize_customer_id("John, 123") == "John123"

    def test_normalize_customer_id_with_dots(self):
        """Dots are removed from customer ID."""
        assert _normalize_customer_id("John.123") == "John123"

    def test_normalize_customer_id_with_hyphens(self):
        """Hyphens are removed from customer ID."""
        assert _normalize_customer_id("John-123") == "John123"

    def test_normalize_customer_id_empty(self):
        """Empty string returns empty."""
        assert _normalize_customer_id("") == ""

    def test_normalize_customer_id_invalid_chars(self):
        """Invalid characters return empty."""
        assert _normalize_customer_id("John@123") == ""

    def test_normalize_pin_with_spaces(self):
        """Spaces are removed from PIN."""
        assert _normalize_pin("1 2 3 4") == "1234"

    def test_normalize_pin_with_commas(self):
        """Commas are removed from PIN."""
        assert _normalize_pin("1,2,3,4") == "1234"

    def test_normalize_pin_with_dots(self):
        """Dots are removed from PIN."""
        assert _normalize_pin("1.2.3.4") == "1234"

    def test_normalize_pin_empty(self):
        """Empty string returns empty."""
        assert _normalize_pin("") == ""


# ============================================================
# 4. TOOL EXECUTION TESTS (After Verification)
# ============================================================

class TestToolExecution:
    """Test tool execution after successful verification."""

    def test_get_balance_after_verification(self):
        """Balance returns correct data after verification."""
        verify_identity_raw("John123", "1234")
        result = get_account_balance.invoke({"customer_id": "John123"})
        assert result["available"] == 5000.00
        assert result["currency"] == "USD"
        assert result["type"] == "checking"
        assert result["customer_id"] == "John123"

    def test_get_transactions_after_verification(self):
        """Transactions return correct data after verification."""
        verify_identity_raw("John123", "1234")
        result = get_recent_transactions.invoke({"customer_id": "John123", "count": 3})
        assert len(result) == 3
        assert result[0]["merchant"] == "Walmart"
        assert result[0]["amount"] == -50.00
        assert result[1]["merchant"] == "Netflix"

    def test_get_transactions_respects_count(self):
        """Transaction count parameter is respected."""
        verify_identity_raw("John123", "1234")
        result = get_recent_transactions.invoke({"customer_id": "John123", "count": 1})
        assert len(result) == 1

    def test_get_profile_after_verification(self):
        """Profile returns correct data after verification."""
        verify_identity_raw("John123", "1234")
        result = get_customer_profile.invoke({"customer_id": "John123"})
        assert result["name"] == "John Doe"
        assert result["email"] == "john.doe@example.com"
        assert result["phone"] == "+1-202-555-0100"
        assert "12 Main St" in result["address"]

    def test_get_cards_after_verification(self):
        """Cards return correct data after verification."""
        verify_identity_raw("John123", "1234")
        result = get_customer_cards.invoke({"customer_id": "John123"})
        assert len(result) == 1
        assert result[0]["card_id"] == "card_123"
        assert result[0]["network"] == "VISA"
        assert result[0]["last4"] == "4242"

    def test_block_card_after_verification(self):
        """Card blocking works after verification."""
        verify_identity_raw("John123", "1234")
        result = block_card.invoke({"card_id": "card_123", "reason": "lost"})
        assert "blocked" in result.lower()
        assert MOCK_DB["cards"]["card_123"]["status"] == "blocked"

    def test_request_statement_existing_period(self):
        """Statement request works for existing period."""
        verify_identity_raw("John123", "1234")
        result = request_statement.invoke({"customer_id": "John123", "period": "2025-12"})
        assert result["status"] == "ready"
        assert result["statement_id"] == "st_202512"

    def test_request_statement_nonexistent_period(self):
        """Statement request returns error for nonexistent period."""
        verify_identity_raw("John123", "1234")
        result = request_statement.invoke({"customer_id": "John123", "period": "2020-01"})
        assert result.get("error") == "statement_not_found"
        assert "available_periods" in result

    def test_update_address(self):
        """Address update works after verification."""
        verify_identity_raw("John123", "1234")
        new_address = "456 New Street, Chicago, IL 60601"
        result = update_address.invoke({"customer_id": "John123", "new_address": new_address})
        assert result["status"] == "updated"
        assert result["address"] == new_address

    def test_report_dispute(self):
        """Dispute reporting works after verification."""
        verify_identity_raw("John123", "1234")
        result = report_cash_not_dispensed.invoke({
            "customer_id": "John123",
            "atm_id": "ATM001",
            "amount": 200.0,
            "date": "2026-01-25"
        })
        assert result["status"] == "submitted"
        assert "dispute_id" in result


# ============================================================
# 5. API ENDPOINT TESTS
# ============================================================

@pytest.mark.skipif(not HAS_API_DEPS, reason="FastAPI dependencies not installed")
class TestAPIEndpoints:
    """Test FastAPI endpoints."""

    def test_health_check(self):
        """Health check endpoint returns healthy status."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Bank ABC Voice Agent"

    def test_call_start(self):
        """Call start creates a new session."""
        response = client.post("/call/start", data={"env_key": "dev"})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["agent_response"] == "Hello, welcome to Bank ABC. How can I help you today?"
        assert data["is_verified"] is False

    def test_call_end(self):
        """Call end closes the session."""
        # First start a call
        start_response = client.post("/call/start", data={"env_key": "dev"})
        session_id = start_response.json()["session_id"]

        # Then end it
        response = client.post("/call/end", data={"session_id": session_id})
        assert response.status_code == 200
        data = response.json()
        assert "Goodbye" in data["agent_response"]

    def test_call_end_invalid_session(self):
        """Call end with invalid session returns 404."""
        response = client.post("/call/end", data={"session_id": "invalid-session-id"})
        assert response.status_code == 404

    def test_sessions_list(self):
        """Sessions endpoint returns list of sessions."""
        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_config_get(self):
        """Config endpoint returns current configuration."""
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "base_system_prompt" in data
        assert "router_prompt" in data


# ============================================================
# 6. EDGE CASES AND ERROR HANDLING
# ============================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_balance_unknown_customer(self):
        """Balance request for unknown customer after setting verification."""
        # Manually set verification for unknown customer
        set_verification_state("Unknown999", True)
        result = get_account_balance.invoke({"customer_id": "Unknown999"})
        # Should fail because customer doesn't exist in MOCK_DB
        assert result.get("error") in ["identity_not_verified", "customer_not_found"]

    def test_block_nonexistent_card(self):
        """Blocking nonexistent card returns error."""
        verify_identity_raw("John123", "1234")
        result = block_card.invoke({"card_id": "nonexistent_card", "reason": "test"})
        assert "not found" in result.lower()

    def test_transaction_count_capped(self):
        """Transaction count is capped at 20."""
        verify_identity_raw("John123", "1234")
        result = get_recent_transactions.invoke({"customer_id": "John123", "count": 100})
        # Should not exceed available transactions or cap
        assert len(result) <= 20

    def test_transaction_count_minimum(self):
        """Transaction count has minimum of 1."""
        verify_identity_raw("John123", "1234")
        result = get_recent_transactions.invoke({"customer_id": "John123", "count": 0})
        assert len(result) >= 1

    def test_empty_customer_id(self):
        """Empty customer ID returns appropriate error."""
        result = get_account_balance.invoke({"customer_id": ""})
        assert result.get("error") == "identity_not_verified"

    def test_verification_persists_across_tools(self):
        """Verification state persists across multiple tool calls."""
        verify_identity_raw("John123", "1234")

        # Multiple tool calls should all work
        balance = get_account_balance.invoke({"customer_id": "John123"})
        assert "error" not in balance

        transactions = get_recent_transactions.invoke({"customer_id": "John123", "count": 2})
        assert "error" not in transactions[0]

        profile = get_customer_profile.invoke({"customer_id": "John123"})
        assert "error" not in profile


# ============================================================
# 7. COMPLETE FLOW TESTS (End-to-End Scenarios)
# ============================================================

class TestCompleteFlows:
    """Test complete user flows as described in doc.txt."""

    def test_balance_check_flow(self):
        """Complete balance check flow: verify -> get balance."""
        # Step 1: Verify identity
        verified = verify_identity_raw("John123", "1234")
        assert verified is True

        # Step 2: Get balance
        balance = get_account_balance.invoke({"customer_id": "John123"})
        assert balance["available"] == 5000.00
        assert balance["currency"] == "USD"

    def test_lost_card_flow(self):
        """Complete lost card flow: verify -> block card."""
        # Step 1: Verify identity
        verified = verify_identity_raw("John123", "1234")
        assert verified is True

        # Step 2: Block the card
        result = block_card.invoke({"card_id": "card_123", "reason": "Lost card reported by customer"})
        assert "blocked" in result.lower()

        # Verify card is actually blocked
        assert MOCK_DB["cards"]["card_123"]["status"] == "blocked"

    def test_transaction_history_flow(self):
        """Complete transaction history flow: verify -> get transactions."""
        # Step 1: Verify identity
        verified = verify_identity_raw("John123", "1234")
        assert verified is True

        # Step 2: Get recent transactions
        transactions = get_recent_transactions.invoke({"customer_id": "John123", "count": 3})
        assert len(transactions) == 3

        # Verify transaction details
        assert transactions[0]["merchant"] == "Walmart"
        assert transactions[0]["amount"] == -50.00
        assert transactions[0]["status"] == "completed"

    def test_address_update_flow(self):
        """Complete address update flow: verify -> update address."""
        # Step 1: Verify identity
        verified = verify_identity_raw("John123", "1234")
        assert verified is True

        # Step 2: Update address
        new_address = "789 Updated Ave, New City, NY 10001"
        result = update_address.invoke({"customer_id": "John123", "new_address": new_address})
        assert result["status"] == "updated"

        # Verify address is actually updated
        profile = get_customer_profile.invoke({"customer_id": "John123"})
        assert profile["address"] == new_address

    def test_atm_dispute_flow(self):
        """Complete ATM dispute flow: verify -> report dispute."""
        # Step 1: Verify identity
        verified = verify_identity_raw("John123", "1234")
        assert verified is True

        # Step 2: Report cash not dispensed
        result = report_cash_not_dispensed.invoke({
            "customer_id": "John123",
            "atm_id": "ATM_Downtown_001",
            "amount": 500.0,
            "date": "2026-01-28"
        })
        assert result["status"] == "submitted"

        # Verify dispute was created
        dispute_id = result["dispute_id"]
        assert dispute_id in MOCK_DB["disputes"]
        assert MOCK_DB["disputes"][dispute_id]["amount"] == 500.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
