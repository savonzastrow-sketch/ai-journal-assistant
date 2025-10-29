import streamlit as st
from openai import OpenAI
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import pickle
import os
import io

st.set_page_config(page_title="AI Journaling Assistant", layout="centered")

# -----------------------------
# Configuration
# -----------------------------
FOLDER_ID = "1Bg0ZxeC8ZTzha9Ftg0xEGTuK4ARm2jy1"  # Hardcoded Google Drive folder ID
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# -----------------------------
# Google Drive integration
# -----------------------------
def get_drive_service():
    creds = None
    token_path = "token.pickle"

    if os.path.exists(token_path):
        with open(token_path, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": st.secrets["gcp_oauth_client"]["client_id"],
                    "client_secret": st.secrets["gcp_oauth_client"]["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]
                }
            }
            flow = InstalledAppFlow.from_client_config(
                client_config,
                ["https://www.googleapis.com/auth/drive.file"]
            )
            st.info("⚠️ First-time setup: Click the link below, authorize, and paste the code here.")
            creds = flow.run_console()

        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)

drive_service = get_drive_service()

# -----------------------------
# Helper functions
# -----------------------------
def save_entry_to_drive(entry_text):
    now = datetime.now()
    file_name = f"Journal_{now.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    file_metadata = {"name": file_name, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(entry_text.encode("utf-8")), mimetype="text/plain")
    drive_service.files().create(body=file_metadata, media_body=media).execute()

def read_all_entries_from_drive():
    query = f"'{FOLDER_ID}' in parents and mimeType='text/plain'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
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

def ask_ai_about_entries(question):
    entries_text = read_all_entries_from_drive()
    if not entries_text.strip():
        return "No journal entries available yet."

    prompt = f"You are an AI journaling assistant. The user has provided the following journal entries:\n\n{entries_text}\n\nQuestion: {question}\nAnswer concisely based on the entries."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

# -----------------------------
# Streamlit UI - Mobile-Friendly
# -----------------------------
st.title("📝 AI Journaling Assistant")

# ---- Journal Entry Section ----
st.subheader("Write your journal entry")
entry = st.text_area("", height=250, placeholder="Start typing your journal entry here...")
if st.button("💾 Save Entry"):
    if entry.strip():
        save_entry_to_drive(entry)
        st.success("✅ Entry saved to Google Drive!")
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
