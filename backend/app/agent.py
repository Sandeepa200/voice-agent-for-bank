import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Annotated, TypedDict, Literal, Optional

# Ensure env vars are loaded
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from app.tools import (
    verify_identity,
    get_account_balance,
    get_recent_transactions,
    block_card,
    get_customer_cards,
    request_statement,
    update_address,
    report_cash_not_dispensed,
)


# --- 1. Define State ---
FlowName = Literal[
    "card_atm_issues",
    "account_servicing",
    "account_opening",
    "digital_app_support",
    "transfers_and_bill_payments",
    "account_closure_retention",
]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    customer_id: str
    flow: Optional[FlowName]

# --- 2. Setup LLM & Tools ---
# Using Groq's Llama-3.3-70b-versatile for high intelligence
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.environ.get("GROQ_API_KEY"),
    temperature=0
)

# Bind tools to the LLM
tools = [
    verify_identity,
    get_account_balance,
    get_recent_transactions,
    block_card,
    get_customer_cards,
    request_statement,
    update_address,
    report_cash_not_dispensed,
]
llm_with_tools = llm.bind_tools(tools)

# --- 3. System Prompt ---
BASE_SYSTEM_PROMPT = """You are the AI Voice Agent for Bank ABC. 
Your goal is to assist customers with banking queries efficiently and securely.

SECURITY & VERIFICATION PROTOCOL:
1. The user's Customer ID is: {customer_id}
2. You MUST call `verify_identity(customer_id, pin)` BEFORE calling any of these tools: `get_account_balance`, `get_recent_transactions`, `get_customer_cards`, `request_statement`, `update_address`, `report_cash_not_dispensed`, `block_card`.
3. If the user provides a 4-6 digit number, treat it as a PIN and call `verify_identity`.
4. NEVER reveal balances, transactions, statements, or profile details unless the tools return success (no error).
5. Card blocking is irreversible: confirm reason AND get explicit confirmation before you call `block_card`.

ROUTING:
- You MUST pick exactly one flow label for the user's latest request:
  - card_atm_issues (lost/stolen card, cash not dispensed, declined payments)
  - account_servicing (statement requests, profile updates like address change, balance check)
  - account_opening (stub)
  - digital_app_support (stub)
  - transfers_and_bill_payments (stub)
  - account_closure_retention (stub)
- Current flow: {flow}

STYLE:
- Keep responses SHORT and CONVERSATIONAL (max 2 sentences). This is a voice call.
- Ask for ONE missing detail at a time.

FLOW HANDLING:
- card_atm_issues:
  - Lost/stolen: ask reason and explicit confirmation; after verification, if no card_id, call `get_customer_cards` then call `block_card(card_id, reason)`.
  - Declined payment: ask for merchant or transaction id; after verification, you may call `get_recent_transactions`.
  - Cash not dispensed: ask for ATM id, amount, and date; after verification, call `report_cash_not_dispensed`.
- account_servicing:
  - Balance check: after verification, call `get_account_balance`.
  - Statement request: ask for statement period (YYYY-MM); after verification, call `request_statement`.
  - Address change: ask for new address; after verification, call `update_address`.
- other flows (stubs): give brief POC guidance and offer to capture a callback number.
"""

ROUTER_PROMPT = """Classify the user's latest request into exactly one flow label from:
card_atm_issues, account_servicing, account_opening, digital_app_support, transfers_and_bill_payments, account_closure_retention.
Return ONLY the label, no extra text."""

AGENT_CONFIG = {
    "base_system_prompt": BASE_SYSTEM_PROMPT,
    "router_prompt": ROUTER_PROMPT,
}


def get_agent_config() -> dict:
    return dict(AGENT_CONFIG)


def update_agent_config(*, base_system_prompt: Optional[str] = None, router_prompt: Optional[str] = None) -> dict:
    if base_system_prompt is not None:
        AGENT_CONFIG["base_system_prompt"] = base_system_prompt
    if router_prompt is not None:
        AGENT_CONFIG["router_prompt"] = router_prompt
    return dict(AGENT_CONFIG)

# --- 4. Define Nodes ---
def router(state: AgentState):
    messages = state.get("messages") or []
    last_user_text = ""
    for m in reversed(messages):
        if getattr(m, "type", None) == "human":
            last_user_text = m.content
            break
        if isinstance(m, tuple) and len(m) == 2 and m[0] == "user":
            last_user_text = m[1]
            break

    resp = llm.invoke([SystemMessage(content=AGENT_CONFIG["router_prompt"]), HumanMessage(content=last_user_text or "")])
    label = (resp.content or "").strip()
    allowed = {
        "card_atm_issues",
        "account_servicing",
        "account_opening",
        "digital_app_support",
        "transfers_and_bill_payments",
        "account_closure_retention",
    }
    flow: FlowName = label if label in allowed else "account_servicing"
    return {"flow": flow}


def chatbot(state: AgentState):
    current_prompt = AGENT_CONFIG["base_system_prompt"].format(
        customer_id=state["customer_id"],
        flow=state.get("flow") or "account_servicing",
    )
    
    response = llm_with_tools.invoke([SystemMessage(content=current_prompt)] + state["messages"])
    return {"messages": [response]}

# --- 5. Build Graph ---
graph_builder = StateGraph(AgentState)

graph_builder.add_node("router", router)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode(tools))

# Add edges
graph_builder.add_edge(START, "router")
graph_builder.add_edge("router", "chatbot")
graph_builder.add_conditional_edges(
    "chatbot",
    tools_condition, 
)
graph_builder.add_edge("tools", "chatbot")

# Compile
app = graph_builder.compile()
