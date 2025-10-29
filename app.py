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

st.set_page_config(page_title="AI Journaling Assistant")

# -----------------------------
# Initialize OpenAI client
# -----------------------------
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
                    "redirect_uris": ["http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(
                client_config,
                ["https://www.googleapis.com/auth/drive.file"]
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)

FOLDER_ID = "1Bg0ZxeC8ZTzha9Ftg0xEGTuK4ARm2jy1"
drive_service = get_drive_service()

# -----------------------------
# Helper functions
# -----------------------------
def save_entry_to_drive(entry_text):
    """Save journal entry as a text file in Google Drive"""
    now = datetime.now()
    file_name = f"Journal_{now.strftime('%Y-%m-%d_%H-%M-%S')}.txt"

    file_metadata = {"name": file_name, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(entry_text.encode("utf-8")), mimetype="text/plain")
    drive_service.files().create(body=file_metadata, media_body=media).execute()

def read_all_entries_from_drive():
    """Read all journal entries from the folder"""
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
    """Send the combined journal entries and question to OpenAI"""
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
# Streamlit UI
# -----------------------------
st.title("AI Journaling Assistant")

# Journal entry input
entry = st.text_area("Write your journal entry here:", height=200)
if st.button("Save Entry"):
    if entry.strip():
        save_entry_to_drive(entry)
        st.success("Entry saved to Google Drive!")
    else:
        st.warning("Please write something before saving.")

# AI analysis
st.subheader("Ask AI about your journal")
question = st.text_input("Ask a question about your past entries:")
if st.button("Get AI Insights") and question.strip():
    with st.spinner("Analyzing your journal..."):
        answer = ask_ai_about_entries(question)
        st.success(answer)
