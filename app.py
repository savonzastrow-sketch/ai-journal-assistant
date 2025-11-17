import streamlit as st
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
import io
import json
import urllib.parse

st.set_page_config(page_title="AI Journaling Assistant", layout="centered")

# -----------------------------
# Configuration
# -----------------------------
FOLDER_ID = "0AOJV_s4TPqDcUk9PVA"  # Replace with your Shared Drive folder ID
DIALOGUE_FOLDER_NAME = "ai_dialogues"  # folder name within FOLDER_ID for chats
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
# Helper functions (journals)
# -----------------------------
def get_or_create_monthly_file():
    """Find or create a monthly journal file (e.g., Journal_2025-11.txt)."""
    now = datetime.now(ZoneInfo("America/New_York"))
    month_file_name = f"Journal_{now.strftime('%Y-%m')}.txt"

    query = f"'{FOLDER_ID}' in parents and name='{month_file_name}' and mimeType='text/plain' and trashed=false"
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
        query = f"'{FOLDER_ID}' in parents and mimeType='text/plain' and trashed=false"
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
    """Existing prompt logic that uses journal content as context."""
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
# Helper functions (dialogues)
# -----------------------------
def get_or_create_dialogue_folder():
    """Ensure a folder exists inside FOLDER_ID to hold conversation JSON files; return folder id."""
    # Search for folder with name DIALOGUE_FOLDER_NAME under FOLDER_ID
    q = f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and name='{DIALOGUE_FOLDER_NAME}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    # create folder under FOLDER_ID
    metadata = {
        "name": DIALOGUE_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [FOLDER_ID]
    }
    folder = drive_service.files().create(body=metadata, fields="id").execute()
    return folder["id"]

def list_dialogue_threads():
    """Return list of thread names (without .json) in dialogue folder."""
    folder_id = get_or_create_dialogue_folder()
    q = f"'{folder_id}' in parents and mimeType='application/json' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    names = [f["name"][:-5] if f["name"].endswith(".json") else f["name"] for f in files]
    return sorted(names, key=lambda x: x.lower())

def load_dialogue_thread(name):
    """Load thread JSON by name; return dict with 'name' and 'messages' list."""
    folder_id = get_or_create_dialogue_folder()
    filename = f"{name}.json"
    q = f"'{folder_id}' in parents and name='{filename}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    if not files:
        return {"name": name, "messages": []}
    file_id = files[0]["id"]
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    content = fh.read().decode("utf-8")
    try:
        data = json.loads(content)
    except Exception:
        # If file is not valid JSON, return empty thread but keep name
        return {"name": name, "messages": []}
    return data

def save_dialogue_thread(data):
    """Save thread dict to a JSON file in the dialogue folder (create or update)."""
    folder_id = get_or_create_dialogue_folder()
    name = data.get("name", "unnamed_thread")
    filename = f"{name}.json"
    # Check if exists
    q = f"'{folder_id}' in parents and name='{filename}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    if files:
        file_id = files[0]["id"]
        media = MediaIoBaseUpload(io.BytesIO(content_str.encode("utf-8")), mimetype="application/json")
        drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
    else:
        metadata = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(content_str.encode("utf-8")), mimetype="application/json")
        drive_service.files().create(body=metadata, media_body=media, supportsAllDrives=True).execute()

# -----------------------------
# UI: Use tabs: first tab = existing journaling UI (unchanged logic), second = dialogues
# -----------------------------
tab_journal, tab_dialogue = st.tabs(["📘 Journal", "💬 AI Dialogue"])

# -----------------------------
# Tab 1: Journal (keeps your existing UI/logic intact)
# -----------------------------
with tab_journal:
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
        height=150,  # short box for question
        placeholder="Type your question here..."
    )

    # Buttons below AI section
    col_left, col_spacer, col_right = st.columns([1, 2, 1])
    with col_left:
        if st.button("🤖 Get AI Insights"):
            if st.session_state.question_text.strip():
                with st.spinner("Analyzing your journal entries..."):
                    st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text)
                    pass  # AI answer will be displayed below
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

# -----------------------------
# Tab 2: AI Dialogue Threads
# -----------------------------
with tab_dialogue:
    st.title("💬 AI Dialogue")

    # Thread selection / creation
    st.subheader("Select or create a conversation thread")
    threads = []
    try:
        threads = list_dialogue_threads()
    except Exception as e:
        st.error(f"Failed to list threads: {e}")
        threads = []

    choice = st.selectbox("Choose thread or create new:", ["<Create New Thread>"] + threads)

    if choice == "<Create New Thread>":
        thread_name = st.text_input("New thread name:")
        if not thread_name:
            st.info("Enter a thread name to create it.")
    else:
        thread_name = choice

    # load existing thread (or empty)
    if thread_name:
        try:
            thread = load_dialogue_thread(thread_name)
        except Exception as e:
            st.error(f"Failed to load thread: {e}")
            thread = {"name": thread_name, "messages": []}
    else:
        thread = {"name": thread_name or "unnamed", "messages": []}

    # Display thread messages
    st.markdown("### Conversation")
    if not thread.get("messages"):
        st.info("No messages yet. Start the conversation below.")
    else:
        for msg in thread["messages"]:
            # Show messages with simple formatting
            ts = msg.get("timestamp", "")
            role = msg.get("role", "user")
            if role == "user":
                st.markdown(f"**You** ({ts}):")
                st.write(msg.get("content", ""))
            else:
                st.markdown(f"**AI** ({ts}):")
                st.write(msg.get("content", ""))

    st.markdown("---")

    # New message input
    user_message = st.text_area("Your message:", height=150)

    col_send, col_clear = st.columns([1, 1])
    with col_send:
        if st.button("Send"):
            if not thread_name:
                st.warning("Please provide a thread name first.")
            elif not user_message.strip():
                st.warning("Please type a message before sending.")
            else:
                # append user message
                thread.setdefault("messages", []).append({
                    "role": "user",
                    "content": user_message,
                    "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                })

                # AI response (uses journal context via existing helper)
                with st.spinner("AI is generating a reply..."):
                    ai_reply = ask_ai_about_entries(user_message)

                thread["messages"].append({
                    "role": "assistant",
                    "content": ai_reply,
                    "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                })

                # save thread
                try:
                    thread["name"] = thread_name
                    save_dialogue_thread(thread)
                    st.success("Message sent and thread saved.")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Failed to save thread: {e}")

    with col_clear:
        if st.button("Clear Thread"):
            if thread_name:
                # overwrite with empty messages and save
                empty_thread = {"name": thread_name, "messages": []}
                try:
                    save_dialogue_thread(empty_thread)
                    st.success("Thread cleared.")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Failed to clear thread: {e}")
            else:
                st.warning("No thread selected to clear.")
