import streamlit as st
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
import io

st.set_page_config(page_title="AI Journaling Assistant", layout="centered")

# -----------------------------
# Configuration
# -----------------------------
FOLDER_ID = "0AOJV_s4TPqDcUk9PVA"  # Replace with your Shared Drive folder ID
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# -----------------------------
# Google Drive Service (delegated access)
# -----------------------------
def get_drive_service():
    SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.metadata"
    ]
    SERVICE_ACCOUNT_INFO = st.secrets["gcp_service_account"]
    DELEGATED_EMAIL = "stefan@zeitadvisory.com"  # Your Workspace email

    creds = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=SCOPES
    )
    delegated_creds = creds.with_subject(DELEGATED_EMAIL)

    service = build("drive", "v3", credentials=delegated_creds)
    return service

drive_service = get_drive_service()

# -----------------------------
# Helper functions
# -----------------------------
def get_or_create_monthly_file():
    """Find or create a monthly journal file (e.g., Journal_2025-11.txt)."""
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
        drive_service.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True
        ).execute()

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

def ask_ai_about_entries(question):
    try:
        entries_text = read_all_entries_from_drive()
        if not entries_text.strip():
            return "No journal entries available yet."

        prompt = (
            f"You are an AI journaling assistant. The user has provided the following journal entries:\n\n"
            f"{entries_text}\n\nQuestion: {question}\nAnswer concisely based on the entries."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Failed to get AI insights: {e}"

# -----------------------------
# Streamlit UI
# -----------------------------
st.title("📝 AI Journaling Assistant")

# ---- Journal Entry Section ----
st.subheader("Write your journal entry")

if "entry_text" not in st.session_state:
    st.session_state.entry_text = ""

entry_input = st.text_area(
    "",
    value=st.session_state.entry_text,
    height=250,
    placeholder="Start typing your journal entry here..."
)
st.session_state.entry_text = entry_input

# Buttons below the entry box
col_left, col_spacer, col_right = st.columns([1, 2, 1])
with col_left:
    if st.button("💾 Save Entry"):
        if st.session_state.entry_text.strip():
            success, msg = append_entry_to_monthly_file(st.session_state.entry_text)
            if success:
                st.success(msg)
            else:
                st.error(msg)
        else:
            st.warning("⚠️ Please write something before saving.")
with col_right:
    if st.button("🧹 Clear Entry"):
        st.session_state.entry_text = ""
        st.rerun()
        
st.markdown("---")

# ---- AI Query Section ----
st.subheader("AI Journal Query")

if "question_text" not in st.session_state:
    st.session_state.question_text = ""
if "ai_answer" not in st.session_state:
    st.session_state.ai_answer = ""

st.session_state.question_text = st.text_area(
    "",
    value=st.session_state.question_text,
    height=150,  # Same height as journal entry box
    placeholder="Type your question here..."
)

# Buttons below AI section
col_left, col_spacer, col_right = st.columns([1, 2, 1])
with col_left:
    if st.button("🤖 Get AI Insights"):
        if st.session_state.question_text.strip():
            with st.spinner("Analyzing your journal entries..."):
                st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text)
                pass  # AI answer will be displayed later below
        else:
            st.warning("⚠️ Please type a question before asking.")
with col_right:
    if st.button("🧹 Clear Q&A"):
        st.session_state.question_text = ""
        st.session_state.ai_answer = ""
        st.rerun()

# Display AI answer
if st.session_state.ai_answer:
    st.markdown("### 💡 AI Response:")
    st.markdown(
        f"<div style='background-color:#f0f2f6; padding:1rem; border-radius:10px;'>{st.session_state.ai_answer}</div>",
        unsafe_allow_html=True
    )
