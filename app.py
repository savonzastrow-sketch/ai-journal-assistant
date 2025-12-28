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
    if not entries_text.strip(): return "No journal entries available."
    prompt = f"You are an AI journaling assistant. Based on these entries:\n\n{entries_text}\n\nQuestion: {question}\nAnswer concisely."
    
    try:
        if model_type == "Gemini":
            response = gemini_client.models.generate_content(model='gemini-2.5-flash', contents=[prompt])
            return response.text
        else:
            response = openai_client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
            return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ {model_type} error: {e}"

def get_last_30_days_data():
    """Parses the last 30 days of structured data from the journal text."""
    all_text = read_all_entries_from_drive()
    lines = all_text.split('\n')
    
    data = []
    current_date = None
    
    # Simple parsing logic looking for your template headers
    for i, line in enumerate(lines):
        if "🗓️" in line:
            # Extract date like 'December 27, 2025'
            try:
                date_str = line.split("🗓️ ")[1].split(" at")[0].strip()
                current_date = datetime.strptime(date_str, '%B %d, %Y').date()
            except: continue
            
        if "DAILY TEMPLATE SUMMARY:" in line and current_date:
            # Look ahead for the values in the next few lines
            entry = {"Date": current_date, "Satisfaction": np.nan, "Neuralgia": np.nan, "Exercise_Mins": 0}
            for j in range(i, i+10): # Look at the next 10 lines
                if j >= len(lines): break
                    if "- Satisfaction:" in lines[j]:
                        entry["Satisfaction"] = float(lines[j].split(":")[1].split("/")[0])
                    if "- Neuralgia:" in lines[j]:
                        entry["Neuralgia"] = float(lines[j].split(":")[1].split("/")[0])
                    if "- Exercise:" in lines[j]:
                        # Extract '30' from 'Swim (30 mins, 2.0 distance)'
                        try: entry["Exercise_Mins"] = float(lines[j].split("(")[1].split(" mins")[0])
                        except: pass
            data.append(entry)

    df = pd.DataFrame(data)
    if df.empty: return pd.DataFrame()
    
    # Ensure the last 30 days are present, even if empty
    end_date = datetime.now().date()
    start_date = end_date - pd.Timedelta(days=29)
    all_days = pd.date_range(start_date, end_date).date
    
    df = df.drop_duplicates('Date').set_index('Date').reindex(all_days)
    df.index.name = 'Date'
    return df.reset_index()

# --- STREAMLIT UI ---

st.title("📝 AI Journaling Suite")

# Initialize session state
if "entry_text" not in st.session_state: st.session_state.entry_text = ""
if "question_text" not in st.session_state: st.session_state.question_text = ""
if "ai_answer" not in st.session_state: st.session_state.ai_answer = ""
if "model_used" not in st.session_state: st.session_state.model_used = ""

# 1. CREATE TABS
tab1, tab2, tab3 = st.tabs(["🤖 AI Assistant", "📋 Daily Template", "📊 30-Day Recap"])

# --- TAB 1: ORIGINAL AI ASSISTANT ---
with tab1:
    st.subheader("Free-form Journal Entry")
    entry_input = st.text_area("", value=st.session_state.entry_text, height=200, key='entry_area', placeholder="Write freely here...")
    st.session_state.entry_text = entry_input

    col_l, col_s, col_r = st.columns([1, 2, 1])
    with col_l:
        if st.button("💾 Save Entry", use_container_width=True):
            if st.session_state.entry_text.strip():
                success, msg = append_entry_to_monthly_file(st.session_state.entry_text)
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

# --- TAB 2: STRUCTURED DAILY TEMPLATE ---
with tab2:
    st.subheader("Daily Tracking Template")
    
    with st.expander("1. Summary & Satisfaction", expanded=True):
        t2_summary = st.text_area("What happened today?", placeholder="A quick summary...")
        t2_satisfaction = st.select_slider("Overall Satisfaction (0=Tough, 5=Great)", options=range(6), value=3)

    with st.expander("2. Health Tracking", expanded=True):
        t2_neuralgia = st.select_slider("Neuralgia Rating (0=Good, 5=Bad)", options=range(6), value=0)
        t2_health_notes = st.text_area("Health Notes", placeholder="Describe any specific symptoms or observations...")

    with st.expander("3. Exercise Details", expanded=True):
        c1, c2, c3 = st.columns(3)
        t2_ex_type = c1.selectbox("Activity", ["Swim", "Run", "Cycle", "Elliptical", "Yoga", "Other"])
        t2_ex_time = c2.number_input("Time (mins)", min_value=0)
        t2_ex_dist = c3.number_input("Distance (miles)", min_value=0.0)

    t2_insights = st.text_area("Reflections & Insights")

    if st.button("💾 Submit Template to Drive", use_container_width=True):
        # Format the structured data for the text file
        formatted_template = (
            f"DAILY TEMPLATE SUMMARY:\n"
            f"- Summary: {t2_summary}\n"
            f"- Satisfaction: {t2_satisfaction}/5\n"
            f"- Neuralgia: {t2_neuralgia}/5\n"
            f"- Health Notes: {t2_health_notes}\n"
            f"- Exercise: {t2_ex_type} ({t2_ex_time} mins, {t2_ex_dist} distance)\n"
            f"- Insights: {t2_insights}"
        )
        success, msg = append_entry_to_monthly_file(formatted_template)
        if success: st.success(msg)
        else: st.error(msg)

# --- TAB 3: 30-DAY RECAP & GRAPHICS ---
with tab3:
    st.subheader("Monthly Progress at a Glance")
    
    # Fetch real data
    df_metrics = get_last_30_days_data()

    if not df_metrics.empty:
        # Format dates for the bottom of the chart (e.g., Dec 27)
        df_metrics['Date_Label'] = df_metrics['Date'].apply(lambda x: x.strftime('%b %d') if pd.notnull(x) else "")
        
        # 1. Line Chart for Health Ratings
        st.write("### Satisfaction vs. Neuralgia")
        st.line_chart(df_metrics.set_index('Date_Label')[['Satisfaction', 'Neuralgia']])
        
        # 2. Bar Chart for Exercise Minutes
        st.write("### Exercise Minutes per Day")
        st.bar_chart(df_metrics.set_index('Date_Label')['Exercise_Mins'], color="#ffaa00")
    else:
        st.info("No template data found yet. Start saving entries in the 'Daily Template' tab to see your progress!")
    
    st.markdown("---")
    st.subheader("Gemini Monthly Synthesis")
    if st.button("🧠 Generate AI Monthly Report", use_container_width=True):
        with st.spinner("Analyzing patterns in your health and mood..."):
            synthesis_q = "Look at my entries for the last 30 days. Specifically summarize my neuralgia levels vs my activity types and suggest any patterns you see."
            report = ask_ai_about_entries(synthesis_q, "Gemini")
            st.markdown(report)
