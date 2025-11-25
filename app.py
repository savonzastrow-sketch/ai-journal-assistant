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

# --- REMOVED HARDCODED GEMINI KEY FOR TESTING ---

try:
    # Use st.secrets exclusively—the standard Streamlit way
    gemini_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    # The error message is clearer now as it points back to secrets.toml
    st.error(f"Gemini Client Initialization Error: 'st.secrets has no key \"GEMINI_API_KEY\"'. Check your secrets.toml.")

# --- Google Drive Service (Delegated Access) ---
def get_drive_service():
    """Authenticates and returns the Google Drive service object."""
    SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.metadata"
    ]
    SERVICE_ACCOUNT_INFO = st.secrets["gcp_service_account"]
    
    creds = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=SCOPES
    )
    delegated_creds = creds.with_subject(DELEGATED_EMAIL)
    service = build("drive", "v3", credentials=delegated_creds)
    return service

try:
    drive_service = get_drive_service()
except Exception as e:
    st.error(f"Google Drive Service Initialization Error: {e}")
    # st.stop() # Removed st.stop() to allow app to run if Drive fails but AI works

# -----------------------------
# Helper functions (Drive Operations)
# -----------------------------

def get_or_create_monthly_file():
    """Find or create a monthly journal file (e.g., Journal_YYYY-MM.txt)."""
    now = datetime.now(ZoneInfo("America/New_York"))
    month_file_name = f"Journal_{now.strftime('%Y-%m')}.txt"
    
    query = f"'{FOLDER_ID}' in parents and name='{month_file_name}' and mimeType='text/plain'"
    results = drive_service.files().list(
        q=query, 
        fields="files(id, name)", 
        supportsAllDrives=True, 
        includeItemsFromAllDrives=True
    ).execute()
    
    files = results.get("files", [])
    
    if files:
        return files[0]["id"]
    else:
        file_metadata = {"name": month_file_name, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain")
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True
        ).execute()
        return file["id"]

def append_entry_to_monthly_file(entry_text):
    """Append a new journal entry to the current month's file."""
    try:
        file_id = get_or_create_monthly_file()
        
        # 1. Download existing content
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        fh.seek(0)
        existing_content = fh.read().decode("utf-8")
        
        # 2. Prepare new entry
        now = datetime.now(ZoneInfo("America/New_York"))
        new_entry = f"\n\n---\n🗓️ {now.strftime('%B %d, %Y %I:%M %p EST')}\n{entry_text.strip()}\n"
        updated_content = existing_content + new_entry
        
        # 3. Upload updated content
        media = MediaIoBaseUpload(io.BytesIO(updated_content.encode("utf-8")), mimetype="text/plain")
        drive_service.files().update(
            fileId=file_id, 
            media_body=media, 
            supportsAllDrives=True
        ).execute()
        
        # Reset entry field on success
        if "entry_text" in st.session_state:
            st.session_state.entry_text = ""
            st.rerun()

        return True, f"✅ Entry saved to {now.strftime('%B %Y')} journal!"
    except Exception as e:
        return False, f"⚠️ Failed to save entry: {e}"

@st.cache_data(ttl=300)
def read_all_entries_from_drive():
    """Read all monthly journal files."""
    try:
        query = f"'{FOLDER_ID}' in parents and mimeType='text/plain'"
        results = drive_service.files().list(
            q=query, 
            fields="files(id, name)", 
            supportsAllDrives=True, 
            includeItemsFromAllDrives=True
        ).execute()
        
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
        st.error(f"⚠️ Failed to read entries: {e}")
        return ""

# --- Helper functions (AI Operations) ---

def ask_openai_about_entries(question):
    """Generates an AI response using the OpenAI API."""
    try:
        entries_text = read_all_entries_from_drive()
        
        if not entries_text.strip():
            return "No journal entries available yet."

        prompt = (
            f"You are an AI journaling assistant. The user has provided the following journal entries:\n\n"
            f"{entries_text}\n\nQuestion: {question}\nAnswer concisely based on the entries."
        )

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Failed to get OpenAI insights: {e}"

def ask_gemini_about_entries(question):
    """Generates an AI response using the Gemini API."""
    try:
        entries_text = read_all_entries_from_drive()
        
        if not entries_text.strip():
            return "No journal entries available yet."

        prompt = (
            f"You are an AI journaling assistant. The user has provided the following journal entries:\n\n"
            f"{entries_text}\n\nQuestion: {question}\nAnswer concisely based on the entries."
        )

        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt]
        )
        return response.text
    except Exception as e:
        return f"⚠️ Failed to get Gemini insights: {e}"

# -----------------------------
# Streamlit UI
# -----------------------------

st.title("📝 AI Journaling Assistant (Gemini/OpenAI)")

# Initialize session state keys
if "entry_text" not in st.session_state: st.session_state.entry_text = ""
if "question_text" not in st.session_state: st.session_state.question_text = ""
if "ai_answer" not in st.session_state: st.session_state.ai_answer = ""
if "model_used" not in st.session_state: st.session_state.model_used = ""


# --- Journal Entry Section ---
st.subheader("Write your journal entry")

entry_input = st.text_area(
    "", 
    value=st.session_state.entry_text, 
    height=250, 
    key='entry_input_area',
    placeholder="Start typing your journal entry here..."
)
st.session_state.entry_text = entry_input

# Buttons below the entry box
col_left, col_spacer, col_right = st.columns([1, 2, 1])

with col_left:
    if st.button("💾 Save Entry", use_container_width=True):
        if st.session_state.entry_text.strip():
            success, msg = append_entry_to_monthly_file(st.session_state.entry_text)
            if success:
                st.success(msg)
            else:
                st.error(msg)
        else:
            st.warning("⚠️ Please write something before saving.")

with col_right:
    if st.button("🧹 Clear Entry", use_container_width=True):
        st.session_state.entry_text = ""
        st.rerun()

st.markdown("---")

# --- AI Query Section ---
st.subheader("AI Journal Query")

st.session_state.question_text = st.text_area(
    "", 
    value=st.session_state.question_text, 
    height=150, 
    key='question_input_area',
    placeholder="Type your question here (e.g., 'What were my main themes last month?')..."
)

# Buttons below AI section: Gemini, OpenAI, Clear
col_g, col_o, col_c = st.columns([1, 1, 1])

with col_g:
    if st.button("✨ Get Gemini Insights", use_container_width=True):
        if st.session_state.question_text.strip():
            with st.spinner("Analyzing entries with Gemini..."):
                st.session_state.ai_answer = ask_gemini_about_entries(st.session_state.question_text)
                st.session_state.model_used = "Gemini"
        else:
            st.warning("⚠️ Please type a question before asking.")

with col_o:
    if st.button("🤖 Get OpenAI Insights", use_container_width=True):
        if st.session_state.question_text.strip():
            with st.spinner("Analyzing entries with OpenAI..."):
                st.session_state.ai_answer = ask_openai_about_entries(st.session_state.question_text)
                st.session_state.model_used = "OpenAI"
        else:
            st.warning("⚠️ Please type a question before asking.")

with col_c:
    if st.button("🧹 Clear Q&A", use_container_width=True):
        st.session_state.question_text = ""
        st.session_state.ai_answer = ""
        st.session_state.model_used = ""
        st.rerun()

# Display AI answer
if st.session_state.ai_answer:
    st.markdown(f"### 💡 AI Response ({st.session_state.model_used}):")
    st.markdown(
        f"<div style='background-color:#f0f2f6; padding:1rem; border-radius:10px;'>{st.session_state.ai_answer}</div>", 
        unsafe_allow_html=True
    )
