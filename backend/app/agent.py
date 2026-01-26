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
    get_verification_status,
    get_account_balance,
    get_customer_profile,
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
_DEFAULT_PRIMARY_MODEL = os.environ.get("GROQ_CHAT_MODEL") or os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
_DEFAULT_FALLBACKS = ",".join(
    [
        _DEFAULT_PRIMARY_MODEL,
        "llama-3.1-8b-instant",
        "llama-3.1-70b-versatile",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
    ]
)
_MODEL_CANDIDATES = [
    m.strip()
    for m in (os.environ.get("GROQ_MODEL_FALLBACKS") or _DEFAULT_FALLBACKS).split(",")
    if m.strip()
]

_LLM_CACHE: dict[str, ChatGroq] = {}
_LLM_WITH_TOOLS_CACHE: dict[str, object] = {}
_ACTIVE_MODEL: str = _MODEL_CANDIDATES[0] if _MODEL_CANDIDATES else _DEFAULT_PRIMARY_MODEL


def _is_rate_limited(exc: Exception) -> bool:
    s = str(exc).lower()
    return "error code: 429" in s or "rate_limit_exceeded" in s or "rate limit reached" in s


def _get_llm(model: str) -> ChatGroq:
    cached = _LLM_CACHE.get(model)
    if cached is not None:
        return cached
    created = ChatGroq(
        model=model,
        api_key=os.environ.get("GROQ_API_KEY"),
        temperature=0,
    )
    _LLM_CACHE[model] = created
    return created

# Bind tools to the LLM
tools = [
    verify_identity,
    get_verification_status,
    get_account_balance,
    get_customer_profile,
    get_recent_transactions,
    block_card,
    get_customer_cards,
    request_statement,
    update_address,
    report_cash_not_dispensed,
]


def _get_llm_with_tools(model: str):
    cached = _LLM_WITH_TOOLS_CACHE.get(model)
    if cached is not None:
        return cached
    bound = _get_llm(model).bind_tools(tools)
    _LLM_WITH_TOOLS_CACHE[model] = bound
    return bound


def _invoke_llm_with_fallback(*, system_prompt: str, messages: list, with_tools: bool):
    global _ACTIVE_MODEL
    ordered = [_ACTIVE_MODEL] + [m for m in _MODEL_CANDIDATES if m != _ACTIVE_MODEL]
    last_exc: Optional[Exception] = None
    for model in ordered:
        try:
            if with_tools:
                llm_obj = _get_llm_with_tools(model)
            else:
                llm_obj = _get_llm(model)
            resp = llm_obj.invoke([SystemMessage(content=system_prompt)] + messages)
            _ACTIVE_MODEL = model
            return resp
        except Exception as e:
            last_exc = e
            if _is_rate_limited(e):
                continue
            continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No model candidates available")

# --- 3. System Prompt ---

# --- 3. System Prompt ---
BASE_SYSTEM_PROMPT = """You are the AI Voice Agent for Bank ABC. 
Your goal is to assist customers with banking queries efficiently and securely.

CONVERSATION & STYLE:
- If the user is greeting, thanking you, or making small talk without a banking request, respond naturally and ask what you can help with.
- Do not ask for Customer ID or PIN until the user asks for something that requires account access. [Very Important]
- Keep replies short and conversational (max 2 sentences). Ask one question at a time.
- If the request is unclear, ask one clarifying question instead of guessing.


SECURITY & VERIFICATION PROTOCOL (CRITICAL):
- Current customer_id: {customer_id}
- If customer_id is not \"guest\", call `get_verification_status(customer_id)` before asking for a PIN.
- Before using any sensitive tool, you must be verified. If not verified: ask for Customer ID (if missing), then ask for PIN (4–6 digits), then call `verify_identity(customer_id, pin)`.
- Never use these tools unless verification succeeded: `get_account_balance`, `get_customer_profile`, `get_recent_transactions`, `get_customer_cards`, `request_statement`, `update_address`, `report_cash_not_dispensed`, `block_card`.
- Never reveal tool syntax. If verification fails, allow one retry, otherwise offer to connect them to a specialist.
- Card blocking is irreversible: confirm the reason and get explicit confirmation before calling `block_card`.

ROUTING:
- You MUST pick exactly one flow label for the user's latest request:
  - card_atm_issues (lost/stolen card, cash not dispensed, declined payments)
  - account_servicing (statement requests, profile updates like address change, balance check)
  - account_opening (stub)
  - digital_app_support (stub)
  - transfers_and_bill_payments (stub)
  - account_closure_retention (stub)
- Current flow: {flow}

FLOW PLAYBOOKS (KEEP IT BRIEF):
- card_atm_issues: After verification, ask for the minimum details needed, then use the right tool (`get_recent_transactions`, `report_cash_not_dispensed`, `get_customer_cards` + `block_card`). For `block_card`, always confirm it’s permanent before acting.
- account_servicing: After verification, use `get_account_balance` / `get_recent_transactions` / `request_statement` / `update_address` / `get_customer_profile` as needed. Ask for one missing input (like statement month or new address) before calling the tool.
- other flows: Give brief guidance and offer to connect them to a specialist.

"""

ROUTER_PROMPT = """You are a classification agent.
Current Conversation Flow: {current_flow}

User's latest message: "{last_user_message}"

Task:
1. If the user is providing information (like ID, PIN, Name), confirming something ("yes", "ok", "thank you"), or continuing the current conversation, KEEP the current flow: {current_flow}.
2. ONLY if the user explicitly asks for a DIFFERENT topic, return the new flow label.
3. If there is no current flow (None/empty), classify based on the message.

Available Flows:
card_atm_issues, account_servicing, account_opening, digital_app_support, transfers_and_bill_payments, account_closure_retention.

Return ONLY the flow label. No extra text."""

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

    current_flow = state.get("flow") or "None"
    router_prompt = AGENT_CONFIG["router_prompt"]
    try:
        router_prompt = router_prompt.format(
            last_user_message=last_user_text or "",
            current_flow=current_flow
        )
    except Exception:
        # Fallback in case template keys don't match
        try:
             router_prompt = router_prompt.format(last_user_message=last_user_text or "")
        except:
             pass

    resp = _invoke_llm_with_fallback(
        system_prompt=router_prompt,
        messages=[HumanMessage(content=last_user_text or "")],
        with_tools=False,
    )
    label = (resp.content or "").strip()
    allowed = {
        "card_atm_issues",
        "account_servicing",
        "account_opening",
        "digital_app_support",
        "transfers_and_bill_payments",
        "account_closure_retention",
    }
    
    # If the label is not allowed, check if we should keep the current flow
    if label not in allowed:
        # If current_flow is valid, fallback to it
        if current_flow in allowed:
            flow = current_flow
        else:
            flow = "account_servicing"
    else:
        flow = label
        
    return {"flow": flow}


def chatbot(state: AgentState):
    current_prompt = AGENT_CONFIG["base_system_prompt"].format(
        customer_id=state["customer_id"],
        flow=state.get("flow") or "account_servicing",
    )
    
    response = _invoke_llm_with_fallback(system_prompt=current_prompt, messages=state["messages"], with_tools=True)
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
