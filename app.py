import streamlit as st
from openai import OpenAI
from datetime import datetime
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
def save_entry_to_drive(entry_text):
    try:
        now = datetime.now()
        file_name = f"Journal_{now.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        file_metadata = {"name": file_name, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(io.BytesIO(entry_text.encode("utf-8")), mimetype="text/plain")
        drive_service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True
        ).execute()
        return True, "✅ Entry saved to Google Drive!"
    except Exception as e:
        return False, f"⚠️ Failed to save entry: {e}"

def read_all_entries_from_drive():
    try:
        query = f"'{FOLDER_ID}' in parents and mimeType='text/plain'"
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = results.get("files", [])

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
entry = st.text_area("", height=250, placeholder="Start typing your journal entry here...")
if st.button("💾 Save Entry"):
    if entry.strip():
        success, msg = save_entry_to_drive(entry)
        if success:
            st.success(msg)
        else:
            st.error(msg)
    else:
        st.warning("⚠️ Please write something before saving.")

st.markdown("---")

# ---- AI Query Section ----
st.subheader("Ask AI about your past journal entries")
question = st.text_input("Type your question here:")
if st.button("🤖 Get AI Insights") and question.strip():
    with st.spinner("Analyzing your journal entries..."):
        answer = ask_ai_about_entries(question)
        st.success(answer)
