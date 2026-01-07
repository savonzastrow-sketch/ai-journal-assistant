import streamlit as st
from openai import OpenAI
from google import genai
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
import io
import os
import pandas as pd
import numpy as np
import altair as alt
import gspread

# --- Streamlit Page Configuration ---
st.set_page_config(page_title="AI Journaling Assistant (Gemini/OpenAI)", layout="centered")

# --- Configuration & Initialization ---

# NOTE: REPLACE THE BELOW VALUES WITH YOUR ACTUAL FOLDER ID AND WORKSPACE EMAIL
FOLDER_ID = "0AOJV_s4TPqDcUk9PVA"  # Replace with your Shared Drive folder ID
DELEGATED_EMAIL = "stefan@zeitadvisory.com"  # Your Workspace email (for Drive Service)

# Initialize API Clients using st.secrets
try:
    openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
except Exception as e:
    st.error(f"OpenAI Client Initialization Error: {e}")

try:
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or st.secrets["GEMINI_API_KEY"]
    gemini_client = genai.Client(api_key=GEMINI_KEY)
except Exception as e:
    st.error(f"Gemini Client Initialization Error: {e}")

# --- Google Drive Service (Delegated Access) ---
def get_drive_service():
    SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.metadata"
    ]
    SERVICE_ACCOUNT_INFO = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    delegated_creds = creds.with_subject(DELEGATED_EMAIL)
    return build("drive", "v3", credentials=delegated_creds)

try:
    drive_service = get_drive_service()
except Exception as e:
    st.error(f"Google Drive Service Initialization Error: {e}")

# --- Google Sheets Access (Activity Log) ---
def get_activity_log_data():
    try:
        # Re-use your existing service account secrets
        info = st.secrets["gcp_service_account"]
        client = gspread.service_account_from_dict(info)
        
        # Open the specific sheet by name
        sheet = client.open("Daily Activity Log").sheet1
        all_values = sheet.get_all_values()
        
        if len(all_values) > 1:
            df = pd.DataFrame(all_values[1:], columns=all_values[0])
            return df.to_string(index=False)
        return "No activity log data found."
    except Exception as e:
        return f"Error accessing Activity Log: {e}"

# --- Drive Helper Functions ---

def get_or_create_monthly_file():
    now = datetime.now(ZoneInfo("America/New_York"))
    month_file_name = f"Journal_{now.strftime('%Y-%m')}.txt"
    query = f"'{FOLDER_ID}' in parents and name='{month_file_name}' and mimeType='text/plain'"
    results = drive_service.files().list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = results.get("files", [])
    
    if files:
        return files[0]["id"]
    else:
        file_metadata = {"name": month_file_name, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain")
        file = drive_service.files().create(body=file_metadata, media_body=media, supportsAllDrives=True).execute()
        return file["id"]

def append_entry_to_monthly_file(entry_text):
    try:
        file_id = get_or_create_monthly_file()
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        existing_content = fh.read().decode("utf-8")
        
        now = datetime.now(ZoneInfo("America/New_York"))
        new_entry = f"\n\n---\n🗓️ {now.strftime('%B %d, %Y %I:%M %p EST')}\n{entry_text.strip()}\n"
        updated_content = existing_content + new_entry
        
        media = MediaIoBaseUpload(io.BytesIO(updated_content.encode("utf-8")), mimetype="text/plain")
        drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        return True, f"✅ Entry saved to {now.strftime('%B %Y')} journal!"
    except Exception as e:
        return False, f"⚠️ Failed to save: {e}"

@st.cache_data(ttl=300)
def read_all_entries_from_drive():
    try:
        query = f"'{FOLDER_ID}' in parents and mimeType='text/plain'"
        results = drive_service.files().list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = sorted(results.get("files", []), key=lambda x: x["name"])
        all_text = ""
        for f in files:
            request = drive_service.files().get_media(fileId=f["id"])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            fh.seek(0)
            all_text += fh.read().decode("utf-8") + "\n"
        return all_text
    except Exception as e:
        st.error(f"⚠️ Read error: {e}")
        return ""

# --- AI Helper Functions ---

def ask_ai_about_entries(question, model_type="Gemini"):
    entries_text = read_all_entries_from_drive()
    activity_data = get_activity_log_data()  # Fetch sheet data
    
    if not entries_text.strip() and "Error" in activity_data:
        return "No journal entries or activity data available."
    
    # Combined Prompt for the AI
    prompt = f"""
    You are an AI journaling assistant. You have access to two data sources:
    
    1. JOURNAL ENTRIES:
    {entries_text}
    
    2. ACTIVITY LOG (Exercise & Health Metrics):
    {activity_data}
    
    Question: {question}
    Answer concisely, correlating data from both sources if relevant.
    """
    
    try:
        if model_type == "Gemini":
            response = gemini_client.models.generate_content(model='gemini-2.5-flash', contents=[prompt])
            return response.text
        else:
            response = openai_client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
            return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ {model_type} error: {e}"

# --- STREAMLIT UI ---

st.title("📝 AI Journaling Suite")

# Initialize session state
if "entry_text" not in st.session_state: st.session_state.entry_text = ""
if "question_text" not in st.session_state: st.session_state.question_text = ""
if "ai_answer" not in st.session_state: st.session_state.ai_answer = ""
if "model_used" not in st.session_state: st.session_state.model_used = ""

# --- MAIN INTERFACE (FORMERLY TAB 1) ---
st.subheader("Free-form Journal Entry")

# 1. New Tagging Dropdown Menus
t_col1, t_col2 = st.columns(2)
with t_col1:
    topic_tag = st.selectbox("Topic Tag", ["None", "#health", "#exercise", "#relationships", "#work", "#learning"])
with t_col2:
    signal_tag = st.selectbox("Signal Tag", [
        "None", 
        "#event (something that happened)", 
        "#observation (something you noticed)", 
        "#feeling (emotional state)", 
        "#insight (interpretation or meaning)", 
        "#decision (choice made or planned)", 
        "#question (open question for later)"
    ])

# 2. Entry Text Area
entry_input = st.text_area("", value=st.session_state.entry_text, height=200, key='entry_area', placeholder="Write freely here...")
st.session_state.entry_text = entry_input

col_l, col_s, col_r = st.columns([1, 2, 1])
with col_l:
    if st.button("💾 Save Entry", use_container_width=True):
        if st.session_state.entry_text.strip():
            # Automatically append selected tags to the text
            selected_topic = topic_tag if topic_tag != "None" else ""
            selected_signal = signal_tag.split(" ")[0] if signal_tag != "None" else ""
            tagged_entry = f"{selected_topic} {selected_signal}\n{st.session_state.entry_text}".strip()
            
            success, msg = append_entry_to_monthly_file(tagged_entry)
            if success: st.success(msg)
            else: st.error(msg)
        else: st.warning("⚠️ Write something first.")
with col_r:
    if st.button("🧹 Clear", use_container_width=True):
        st.session_state.entry_text = ""
        st.rerun()

st.markdown("---")
st.subheader("Ask your Journal")
st.session_state.question_text = st.text_area("", value=st.session_state.question_text, height=100, key='q_area', placeholder="Ask about your past entries...")

cg, co, cc = st.columns(3)
with cg:
    if st.button("✨ Gemini", use_container_width=True):
        st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text, "Gemini")
        st.session_state.model_used = "Gemini"
with co:
    if st.button("🤖 OpenAI", use_container_width=True):
        st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text, "OpenAI")
        st.session_state.model_used = "OpenAI"
with cc:
    if st.button("🧹 Clear Q&A", use_container_width=True):
        st.session_state.question_text, st.session_state.ai_answer, st.session_state.model_used = "", "", ""
        st.rerun()

if st.session_state.ai_answer:
    st.markdown(f"### 💡 {st.session_state.model_used} Response:")
    st.info(st.session_state.ai_answer)
