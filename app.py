import streamlit as st
from openai import OpenAI
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os, pickle

# --- OpenAI ---
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# --- Google OAuth (User Login) ---
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def get_drive_service():
    creds = None
    token_file = "token.pkl"

    # Load cached token if exists
    if os.path.exists(token_file):
        with open(token_file, "rb") as token:
            creds = pickle.load(token)

    # If no (or expired) credentials, log in
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_config(
            {"installed": st.secrets["gcp_oauth_client"]}, SCOPES
        )
        creds = flow.run_local_server(port=0)
        with open(token_file, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)

drive_service = get_drive_service()
FOLDER_ID = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]

# --- Streamlit UI ---
st.title("AI Journal Assistant ✍️")

entry = st.text_area("Write your journal entry here:", height=300)
if st.button("Save to Google Drive"):
    if entry.strip():
        filename = f"Journal_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(entry)

        file_metadata = {
            "name": filename,
            "mimeType": "text/plain",
            "parents": [FOLDER_ID],
        }
        media = MediaFileUpload(filename, mimetype="text/plain")
        drive_service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()

        st.success(f"✅ Saved to Google Drive as {filename}")
        os.remove(filename)
    else:
        st.warning("Please write something before saving.")
Replace app.py with Google Drive login version
