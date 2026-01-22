import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Annotated, TypedDict, Literal

# Ensure env vars are loaded
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage
from app.tools import verify_identity, get_account_balance, get_recent_transactions, block_card


# --- 1. Define State ---
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    customer_id: str

# --- 2. Setup LLM & Tools ---
# Using Groq's Llama-3.3-70b-versatile for high intelligence
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.environ.get("GROQ_API_KEY"),
    temperature=0
)

# Bind tools to the LLM
tools = [verify_identity, get_account_balance, get_recent_transactions, block_card]
llm_with_tools = llm.bind_tools(tools)

# --- 3. System Prompt ---
# We inject the customer_id dynamically in the node
BASE_SYSTEM_PROMPT = """You are the AI Voice Agent for Bank ABC. 
Your goal is to assist customers with banking queries efficiently and securely.

SECURITY & VERIFICATION PROTOCOL:
1. The user's Customer ID is: {customer_id}
2. You MUST verify the user's identity using `verify_identity` BEFORE providing any account info (balance, transactions, etc.).
3. Even if you know the Customer ID, you must ask for the PIN if they haven't verified yet.
4. If the user asks to "Block Card", you MUST confirm the reason first.

ROUTING GUIDELINES:
- Keep responses SHORT and CONVERSATIONAL (max 2 sentences). This is a voice call.
- If the user asks about "Card Issues", "Account Servicing", "Digital App", "Transfers", or "Account Closure", handle it or route them accordingly.
- For "Digital App Support" or "Transfers", you can provide general advice but explain you are a POC agent.
"""

# --- 4. Define Nodes ---
def chatbot(state: AgentState):
    # Inject current customer_id into prompt
    current_prompt = BASE_SYSTEM_PROMPT.format(customer_id=state["customer_id"])
    
    # Invoke LLM
    response = llm_with_tools.invoke([SystemMessage(content=current_prompt)] + state["messages"])
    return {"messages": [response]}

# --- 5. Build Graph ---
graph_builder = StateGraph(AgentState)

graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode(tools))

# Add edges
graph_builder.add_edge(START, "chatbot")
graph_builder.add_conditional_edges(
    "chatbot",
    tools_condition, 
)
graph_builder.add_edge("tools", "chatbot")

# Compile
app = graph_builder.compile()


