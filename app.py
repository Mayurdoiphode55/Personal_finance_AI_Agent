# ==============================================================================
# PART 0: IMPORTS AND SETUP
# ==============================================================================
import os
import re
import json
from pathlib import Path
from typing import TypedDict
from dotenv import load_dotenv
import pandas as pd

# --- Core AI and Data Libraries ---
from google.cloud import bigquery
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END

# --- User Interface Library ---
import streamlit as st

# ==============================================================================
# PART 1: ENVIRONMENT AND CONFIGURATION
# ==============================================================================

# Load environment variables from .env file
load_dotenv()

# --- Google Cloud Configuration ---
try:
    credentials_path = Path(__file__).parent / "key.json"
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    GCP_PROJECT_ID = "calcium-scholar-467311-t5"
    BIGQUERY_TABLE_ID = f"{GCP_PROJECT_ID}.finance_data.transactions"
except Exception as e:
    st.error(f"Error setting up Google Cloud credentials path: {e}")
    st.stop()

# --- LLM Initialization ---
if not os.getenv("GOOGLE_API_KEY"):
    st.error("🔴 GOOGLE_API_KEY not found. Please add it to your .env file.")
    st.stop()

try:
    llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro-latest", temperature=0.1)
except Exception as e:
    st.error(f"🔴 Failed to initialize the language model: {e}")
    st.stop()

# ==============================================================================
# PART 2: LANGCHAIN TOOL DEFINITION (BIGQUERY CONNECTOR)
# ==============================================================================

@tool
def get_transaction_data(user_id: str) -> str:
    """
    Retrieves transaction data for a user from BigQuery.
    Returns JSON with transactions ordered by date (most recent first).
    """
    try:
        client = bigquery.Client.from_service_account_json(str(credentials_path))
        query = f"""
            SELECT date, description, amount, category, bank_name
            FROM `{BIGQUERY_TABLE_ID}`
            WHERE user_id = @user_id
            ORDER BY date DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            ]
        )
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()
        transactions = [dict(row) for row in results]
        
        if not transactions:
            return json.dumps({"error": f"No transactions found for user_id '{user_id}'."})
        
        for t in transactions:
            t['date'] = t['date'].isoformat()
        return json.dumps(transactions)
    except Exception as e:
        return json.dumps({"error": f"An error occurred while querying BigQuery: {e}"})

tools = [get_transaction_data]

# ==============================================================================
# PART 3: LANGGRAPH STATE AND AGENT DEFINITIONS
# ==============================================================================

class FinancialGraphState(TypedDict):
    user_id: str
    analysis_result: str
    budget_plan: str
    investment_options: str
    raw_data: str

def analyzer_agent_node(state: FinancialGraphState):
    """Agent to analyze transaction data and produce a formatted report."""
    st.session_state.messages.append("🔍 **Analyzer Agent:** Accessing database and analyzing spending patterns...")
    user_id = state['user_id']
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a meticulous financial analyst AI.

**Formatting Rule**: Your response will be displayed in a UI, so perfect formatting is crucial. Always ensure:
- A single space between words and numbers or symbols (e.g., "$1,234.56", "50%").
- No extra spaces between labels and values (e.g., "Total Income: $3,500.00").
- Markdown tables are clean and aligned.

Start with:

### Metrics Summary
Total Income: $XXXX.XX
Total Spending: $XXXX.XX
Net Flow: $XXXX.XX

Then:

### Executive Summary
A two-sentence friendly overview.

### Spending by Category
A table with "Category", "Total Amount ($)", "Percentage (%)".

### Recurring Subscriptions
A list or "No recurring subscriptions identified."

### Key Insights
- **Top Expense:** Name and amount.
- **Potential Savings:** Highlight a category.

Always double-check formatting before responding.
"""),
        ("human", "Please provide a full financial analysis for the user with ID: {input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    
    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    result = agent_executor.invoke({"input": user_id})
    
    raw_data_result = result.get('intermediate_steps', [({}, '')])[0][1]
    if "error" in raw_data_result:
        return {"analysis_result": f"Failed to retrieve data. Check user ID and database.\nDetails: {raw_data_result}"}
        
    return {"analysis_result": result['output'], "raw_data": raw_data_result}

def budgetor_agent_node(state: FinancialGraphState):
    """Agent to create a personalized budget plan."""
    st.session_state.messages.append("📝 **Budgetor Agent:** Crafting a personalized budget plan...")
    analysis = state['analysis_result']
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a friendly budgeting expert.

**Formatting Rule**: Perfect formatting is essential. Ensure:
- Single space between words and symbols (e.g., "$1,000.00").
- Tables are aligned and headers formatted correctly.

Start with a positive sentence, then create a budget using the 50/30/20 rule.

### Budget Plan
| Category         | Actual Spending ($) | Suggested Budget ($) (50/30/20) | Difference ($) |
|-----------------|--------------------|---------------------------------|----------------|
| Example         | $1,000.00          | $1,200.00                       | +$200.00       |

End with 2-3 actionable recommendations.
"""),
        ("human", "Here is the user's financial analysis:\n\n{analysis}\n\nPlease create a detailed and encouraging budget plan."),
    ])
    
    chain = prompt | llm
    result = chain.invoke({"analysis": analysis})
    return {"budget_plan": result.content}

def investor_agent_node(state: FinancialGraphState):
    """Agent to suggest beginner-friendly investments."""
    st.session_state.messages.append("📈 **Investor Agent:** Identifying beginner-friendly investment options...")
    budget = state['budget_plan']
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a financial educator. Your tone is clear and supportive.

**Formatting Rule**: Formatting must be impeccable:
- Use single spaces only where appropriate.
- Present financial amounts with commas and two decimal points (e.g., "$1,000.00").
- Use bullet points consistently.

Start by identifying the user's potential monthly savings amount.

**--- START OF TEMPLATE ---**
Based on your budget, you have a potential of **$XXX.XX per month** for savings and investments. Here is a sensible plan for a beginner:

- **Option 1: High-Yield Savings Account**
  - **Allocation:** A suggested monthly amount (e.g., "$350 of your $700.00").
  - **Suggestion:** Open a high-yield savings account.
  - **Why it's a good first step:** Explain benefits concisely.

- **Option 2: Index Fund Investing**
  - **Allocation:** Suggested amount.
  - **Suggestion:** Invest in an index fund tracking the S&P 500.
  - **Why it's a good first step:** Explain diversification benefits.

- **Option 3: Roth IRA Contributions**
  - **Allocation:** Suggested amount.
  - **Suggestion:** Start contributing to a Roth IRA.
  - **Why it's a good first step:** Explain tax advantages.

**--- END OF TEMPLATE ---**

End with: **I am not a financial advisor. This information is for educational purposes only and is not financial advice.**
"""),
        ("human", "Here is the user's budget plan:\n\n{budget}\n\nPlease provide personalized investment suggestions."),
    ])
    
    chain = prompt | llm
    result = chain.invoke({"budget": budget})
    return {"investment_options": result.content}

# ==============================================================================
# PART 4: LANGGRAPH WORKFLOW ASSEMBLY
# ==============================================================================

workflow = StateGraph(FinancialGraphState)
workflow.add_node("analyzer", analyzer_agent_node)
workflow.add_node("budgetor", budgetor_agent_node)
workflow.add_node("investor", investor_agent_node)
workflow.set_entry_point("analyzer")
workflow.add_edge("analyzer", "budgetor")
workflow.add_edge("budgetor", "investor")
workflow.add_edge("investor", END)
app = workflow.compile()

# ==============================================================================
# PART 5: STREAMLIT USER INTERFACE
# ==============================================================================

st.set_page_config(page_title="Finance AI Dashboard", page_icon="🤖", layout="wide")

# Parse metrics from the analysis output
def parse_metrics(analysis_result: str):
    metrics = {
        "Total Income": "N/A",
        "Total Spending": "N/A",
        "Net Flow": ("N/A", "off")
    }
    try:
        income_match = re.search(r"Total Income:[\s\xa0]*\$([\d,\.]+)", analysis_result, re.MULTILINE)
        spending_match = re.search(r"Total Spending:[\s\xa0]*\$([\d,\.]+)", analysis_result, re.MULTILINE)
        net_flow_match = re.search(r"Net Flow:[\s\xa0]*\$(-?[\d,\.]+)", analysis_result, re.MULTILINE)

        if income_match:
            metrics["Total Income"] = f"${income_match.group(1)}"
        if spending_match:
            metrics["Total Spending"] = f"${spending_match.group(1)}"
        if net_flow_match:
            net_flow_val = float(net_flow_match.group(1).replace(',', ''))
            delta_color = "inverse" if net_flow_val < 0 else "normal"
            metrics["Net Flow"] = (f"${net_flow_val:,.2f}", delta_color)
    except Exception as e:
        print(f"Error parsing metrics: {e}")
    return metrics

# Sidebar inputs
with st.sidebar:
    st.title("🤖 Finance AI Dashboard")
    user_ids = ["user_001", "user_002", "user_003", "user_004", "user_005"]
    selected_user_id = st.selectbox("Select a User ID to Analyze", user_ids)
    analyze_button = st.button("🚀 Analyze Finances", use_container_width=True)
    st.divider()
    st.info("This app uses AI to analyze finances, create a budget, and suggest investments.")

# Main interface
st.header(f"Financial Report for {selected_user_id}")

if 'results' not in st.session_state:
    st.session_state.results = None
if 'messages' not in st.session_state:
    st.session_state.messages = []

if analyze_button:
    st.session_state.results = None
    st.session_state.messages = []
    with st.status("Running financial analysis...", expanded=True) as status:
        try:
            initial_state = {"user_id": selected_user_id}
            final_state = app.invoke(initial_state, {"recursion_limit": 5})
            st.session_state.results = final_state
            status.update(label="✅ Analysis Complete!", state="complete", expanded=False)
        except Exception as e:
            st.error(f"An error occurred: {e}")
            status.update(label="❌ Analysis Failed!", state="error")

if st.session_state.results:
    results = st.session_state.results
    analysis_content = results.get("analysis_result", "No analysis available.")
    metrics = parse_metrics(analysis_content)

    st.markdown("""
    <style>
        div[data-testid="stMetric"] {
            background-color: rgba(28, 131, 225, 0.1);
            border: 1px solid rgba(28, 131, 225, 0.1);
            padding: 15px;
            border-radius: 10px;
            color: rgb(28, 131, 225);
            overflow-wrap: break-word;
        }
        div[data-testid="stMetric"] > div:nth-child(1) > div:nth-child(2) {
            color: rgba(28, 131, 225, 0.5);
        }
    </style>
    """, unsafe_allow_html=True)

    st.subheader("Financial Overview")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Income", metrics["Total Income"])
    col2.metric("Total Spending", metrics["Total Spending"])
    if isinstance(metrics["Net Flow"], tuple):
        col3.metric("Net Flow", metrics["Net Flow"][0], delta_color=metrics["Net Flow"][1])

    st.divider()
    tab1, tab2, tab3 = st.tabs(["📊 Detailed Analysis", "📋 Budget Plan", "📈 Investment Suggestions"])
    with tab1:
        st.markdown(analysis_content)
    with tab2:
        st.markdown(results.get("budget_plan", "No budget plan available."))
    with tab3:
        st.markdown(results.get("investment_options", "No investment suggestions available."))
else:
    st.info("Select a user from the sidebar and click 'Analyze Finances' to view their report.")

st.markdown("---")
st.markdown("Developed by an AI Assistant | Powered by Google Gemini & LangGraph")
