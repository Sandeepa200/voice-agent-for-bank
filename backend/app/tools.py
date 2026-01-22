from typing import List, Dict, Optional
from langchain_core.tools import tool

# --- Mock Database ---
MOCK_DB = {
    "users": {
        "user_123": {
            "pin": "1234",
            "name": "John Doe",
            "balance": 5000.00,
            "blocked": False,
            "transactions": [
                {"id": "tx_1", "amount": -50.00, "merchant": "Walmart", "status": "completed"},
                {"id": "tx_2", "amount": -12.00, "merchant": "Netflix", "status": "completed"},
                {"id": "tx_3", "amount": -100.00, "merchant": "Unknown", "status": "declined"},
            ]
        }
    }
}

# --- State Management (Simplistic) ---
# In a real app, this would be in Redis or LangGraph state
# For this POC, we'll check this global set, but ideally 
# verification status should be passed in the AgentState.
VERIFIED_USERS = set()

@tool
def verify_identity(customer_id: str, pin: str) -> bool:
    """
    Verifies the customer's identity using their ID and PIN.
    MUST be called before accessing any account details.
    """
    user = MOCK_DB["users"].get(customer_id)
    if user and user["pin"] == pin:
        VERIFIED_USERS.add(customer_id)
        return True
    return False

@tool
def get_account_balance(customer_id: str) -> str:
    """
    Retrieves the account balance. 
    Requires identity verification first.
    """
    if customer_id not in VERIFIED_USERS:
        return "Error: Identity not verified. Please verify identity first."
    
    user = MOCK_DB["users"].get(customer_id)
    return f"${user['balance']:.2f}"

@tool
def get_recent_transactions(customer_id: str, count: int = 3) -> List[Dict]:
    """
    Gets the most recent transactions.
    Requires identity verification first.
    """
    if customer_id not in VERIFIED_USERS:
        return [{"error": "Identity not verified"}]
    
    user = MOCK_DB["users"].get(customer_id)
    return user["transactions"][:count]

@tool
def block_card(customer_id: str, reason: str) -> str:
    """
    Blocks the customer's card immediately. 
    This is an irreversible action.
    """
    if customer_id not in VERIFIED_USERS:
         # For blocking cards, we might be more lenient in real life, 
         # but strict for this assessment's logic
        return "Error: Identity not verified."
    
    user = MOCK_DB["users"].get(customer_id)
    if user:
        user["blocked"] = True
        return f"Card for {user['name']} has been blocked. Reason: {reason}"
    return "User not found."
