import streamlit as st
from openai import OpenAI
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import streamlit as st
import pickle
import os

# Define the OAuth flow
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
                client_config, ["https://www.googleapis.com/auth/drive.file"]
            )

            creds = flow.run_local_server(port=0)

        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)
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

