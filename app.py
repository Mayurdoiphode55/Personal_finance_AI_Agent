# ==============================================================================
# PART 0: IMPORTS AND SETUP
# ==============================================================================
import os
import re
import json
from pathlib import Path
from typing import TypedDict
from dotenv import load_dotenv
import markdown

# --- Core AI and Data Libraries ---
from google.oauth2 import service_account
from google.cloud import bigquery
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.chains import LLMChain
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# --- Web Framework Library ---
from flask import Flask, render_template, request

# ==============================================================================
# PART 1: ENVIRONMENT AND CONFIGURATION
# ==============================================================================

# Load environment variables
load_dotenv()

# --- Google Cloud Configuration ---
GCP_PROJECT_ID = "calcium-scholar-467311-t5"
credentials_path = Path("key.json")  # Make sure key.json is in the same folder

# --- LLM Initialization ---
if not os.getenv("GOOGLE_API_KEY"):
    raise ValueError("🔴 GOOGLE_API_KEY not found in .env")

llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro-latest", temperature=0.1)

# ==============================================================================
# PART 2: BIGQUERY TOOL
# ==============================================================================

@tool
def get_transaction_data(user_id: str) -> list:
    """Fetch transaction data from BigQuery."""
    try:
        credentials = service_account.Credentials.from_service_account_file(str(credentials_path))
        client = bigquery.Client(credentials=credentials, project=GCP_PROJECT_ID)
        table_id = f"{GCP_PROJECT_ID}.finance_data.transactions"

        query = f"""
            SELECT date, description, amount, category, bank_name
            FROM `{table_id}`
            WHERE user_id = @user_id
            ORDER BY date DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
        )
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()
        transactions = [dict(row) for row in results]

        for t in transactions:
            t['date'] = t['date'].isoformat()

        return transactions

    except Exception as e:
        print(f"BigQuery error: {e}")
        return []

tools = [get_transaction_data]

# ==============================================================================
# PART 3: LANGGRAPH STATE AND AGENT DEFINITIONS
# ==============================================================================

class FinancialGraphState(TypedDict):
    user_id: str
    analysis_result: str
    budget_plan: str
    investment_options: str

def analyzer_agent_node(state: FinancialGraphState):
    """Analyze transactions and produce JSON + markdown analysis."""
    print("🔍 Analyzer Agent running...")
    user_id = state['user_id']

    # Fetch transactions
    transactions = get_transaction_data(user_id)

    if not transactions:
        total_income = 0
        total_spending = 0
        net_flow = 0
        summary_text = "No transactions found for this user."
    else:
        total_income = sum(t['amount'] for t in transactions if t['amount'] > 0)
        total_spending = sum(t['amount'] for t in transactions if t['amount'] < 0)
        net_flow = total_income + total_spending

        summary_text = f"User has {len(transactions)} transactions. " \
                       f"Largest transaction: ${max(t['amount'] for t in transactions):,.2f}, " \
                       f"Smallest transaction: ${min(t['amount'] for t in transactions):,.2f}."

    # LLM prompt for detailed markdown report
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a meticulous financial analyst AI."),
        ("human", "Here is the user's transaction summary:\n"
                  "Total Income: {total_income}\n"
                  "Total Spending: {total_spending}\n"
                  "Net Flow: {net_flow}\n\n"
                  "Provide a detailed financial analysis in markdown.")
    ])

    chain = LLMChain(llm=llm, prompt=prompt)
    detailed_analysis = chain.run({
        "total_income": total_income,
        "total_spending": total_spending,
        "net_flow": net_flow
    })


    # Combine JSON + markdown
    metrics_json = json.dumps({
        "total_income": total_income,
        "total_spending": total_spending,
        "net_flow": net_flow
    })

    # Remove any leading 'json {...}' line from LLM output
    detailed_analysis_clean = re.sub(r'^json\s+(\{.*?\})\s*', '', detailed_analysis, flags=re.MULTILINE)

    # Combine JSON metrics with cleaned markdown
    full_analysis = f"```json\n{metrics_json}\n```"
    full_analysis += f"\n\n{detailed_analysis_clean}"

    return {"analysis_result": full_analysis}





def budgetor_agent_node(state: FinancialGraphState):
    print("📝 Budgetor Agent running...")
    analysis = state['analysis_result']

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a friendly budgeting expert."),
        ("human", "Here is the user's financial analysis:\n\n{analysis}\n\n"
                  "Create a detailed, encouraging budget plan.")
    ])

    chain = LLMChain(llm=llm, prompt=prompt)
    budget_plan = chain.run({"analysis": analysis})
    return {"budget_plan": budget_plan}

def investor_agent_node(state: FinancialGraphState):
    print("📈 Investor Agent running...")
    budget = state['budget_plan']

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a financial educator providing beginner-friendly investment advice."),
        ("human", "Here is the user's budget plan:\n\n{budget}\n\n"
                  "Provide personalized investment suggestions.")
    ])

    chain = LLMChain(llm=llm, prompt=prompt)
    investment_options = chain.run({"budget": budget})
    return {"investment_options": investment_options}

# ==============================================================================
# PART 4: WORKFLOW
# ==============================================================================

workflow = StateGraph(FinancialGraphState)
workflow.add_node("analyzer", analyzer_agent_node)
workflow.add_node("budgetor", budgetor_agent_node)
workflow.add_node("investor", investor_agent_node)
workflow.set_entry_point("analyzer")
workflow.add_edge("analyzer", "budgetor")
workflow.add_edge("budgetor", "investor")
workflow.add_edge("investor", END)
app_logic = workflow.compile()

# ==============================================================================
# PART 5: FLASK WEB INTERFACE
# ==============================================================================

app = Flask(__name__)

@app.template_filter('markdown')
def markdown_filter(text):
    return markdown.markdown(text)

def parse_metrics(analysis_result: str):
    metrics = {"Total Income": "N/A", "Total Spending": "N/A", "Net Flow": ("N/A", "text-secondary")}
    try:
        json_match = re.search(r"\{[\s\S]*?\}", analysis_result)
        if json_match:
            data = json.loads(json_match.group(0))
            income = data.get("total_income", 0)
            spending = data.get("total_spending", 0)
            net_flow = data.get("net_flow", 0)

            metrics["Total Income"] = f"${income:,.2f}"
            metrics["Total Spending"] = f"${abs(spending):,.2f}"
            delta_color = "text-danger" if net_flow < 0 else "text-success"
            metrics["Net Flow"] = (f"${net_flow:,.2f}", delta_color)
    except Exception as e:
        print(f"Error parsing metrics: {e}")
    return metrics

@app.route('/', methods=['GET'])
def index():
    user_ids = ["user_001", "user_002", "user_003", "user_004", "user_005"]
    return render_template('index.html', user_ids=user_ids)

@app.route('/analyze', methods=['POST'])
def analyze():
    user_id = request.form.get('user_id')
    if not user_id:
        return render_template('index.html', error="Please select a user ID.")

    print(f"Starting analysis for user: {user_id}")
    initial_state = {"user_id": user_id}
    results = app_logic.invoke(initial_state)
    print("Analysis complete.")

    analysis_content = results.get("analysis_result", "No analysis available.")
    metrics = parse_metrics(analysis_content)

    user_ids = ["user_001", "user_002", "user_003", "user_004", "user_005"]
    return render_template('index.html', results=results, user_id=user_id, metrics=metrics, user_ids=user_ids)

if __name__ == '__main__':
    app.run(debug=True)
