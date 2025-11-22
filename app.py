import streamlit as st
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
import io
import re

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
# Journal helpers (unchanged behavior)
# -----------------------------
def get_or_create_monthly_file():
    now = datetime.now(ZoneInfo("America/New_York"))
    month_file_name = f"Journal_{now.strftime('%Y-%m')}.txt"

    query = (
        f"'{FOLDER_ID}' in parents and name='{month_file_name}' and mimeType='text/plain' and trashed=false"
    )
    res = drive_service.files().list(
        q=query, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": month_file_name, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain")
    file = drive_service.files().create(body=metadata, media_body=media, supportsAllDrives=True, fields="id").execute()
    return file["id"]

def append_entry_to_monthly_file(entry_text):
    try:
        file_id = get_or_create_monthly_file()
        # read existing
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        existing = fh.read().decode("utf-8")

        now = datetime.now(ZoneInfo("America/New_York"))
        new_entry = f"\n\n---\n🗓️ {now.strftime('%B %d, %Y %I:%M %p EST')}\n{entry_text.strip()}\n"
        updated = existing + new_entry

        media = MediaIoBaseUpload(io.BytesIO(updated.encode("utf-8")), mimetype="text/plain")
        drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        return True, f"✅ Entry saved to {now.strftime('%B %Y')} journal!"
    except Exception as e:
        return False, f"⚠️ Failed to save entry: {e}"

@st.cache_data(ttl=300)
def read_all_entries_from_drive():
    try:
        # Include both journal files and thread files
        query = (
            f"'{FOLDER_ID}' in parents and mimeType='text/plain' "
            f"and (name contains 'Journal_' or name contains 'Thread_')"
        )
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = sorted([f for f in results.get("files", []) if f["name"].startswith("Journal_")], key=lambda x: x["name"])
        all_text = ""
        for f in files:
            request = drive_service.files().get_media(fileId=f["id"])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            all_text += fh.read().decode("utf-8") + "\n"
        return all_text
    except Exception:
        return ""

def ask_ai_about_entries(question):
    try:
        entries_text = read_all_entries_from_drive()
        if not entries_text.strip():
            return "No journal entries available yet."
        prompt = (
            f"You are an AI journaling assistant. The user has provided these journal entries:\n\n"
            f"{entries_text}\n\nUser question: {question}\nAnswer concisely based ONLY on journal content."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Failed to get AI insights: {e}"

# -----------------------------
# Dialogue helpers (threads stored in same folder as journals)
# -----------------------------
def list_threads():
    """Return list of files that start with 'Thread_' in the folder (as dicts)."""
    try:
        q = f"'{FOLDER_ID}' in parents and name contains 'Thread_' and trashed=false"
        res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        # return sorted by name (case-insensitive)
        return sorted(files, key=lambda x: x["name"].lower())
    except Exception as e:
        st.error(f"⚠️ Failed to list threads: {e}")
        return []

def create_thread_file(title):
    """Create a Thread_<safe_title>.txt with an initial header and return file id."""
    safe_title = re.sub(r"[^a-zA-Z0-9_]+", "_", title).strip("_")
    if not safe_title:
        safe_title = "unnamed_thread"
    filename = f"Thread_{safe_title}.txt"
    initial = f"Thread: {title}\nCreated: {datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    metadata = {"name": filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(initial.encode("utf-8")), mimetype="text/plain")
    file = drive_service.files().create(body=metadata, media_body=media, supportsAllDrives=True, fields="id").execute()
    return file["id"]

def load_thread_by_id(file_id):
    """Load thread content by file id (returns string)."""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        return fh.read().decode("utf-8")
    except Exception as e:
        st.error(f"⚠️ Failed to load thread (fileId={file_id}): {e}")
        return ""

def save_thread_by_id(file_id, content):
    """Overwrite thread file content by id."""
    try:
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
        drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
    except Exception as e:
        st.error(f"⚠️ Failed to save thread (fileId={file_id}): {e}")

# -----------------------------
# UI with tabs: Journal (unchanged) and AI Dialogue
# -----------------------------
tab_journal, tab_dialogue = st.tabs(["📝 Journal", "💬 AI Dialogue"])

# -----------------------------
# Tab 1: Journal (kept intact)
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
        height=150,
        placeholder="Type your question here..."
    )

    col_left, col_spacer, col_right = st.columns([1, 2, 1])
    with col_left:
        if st.button("🤖 Get AI Insights"):
            if st.session_state.question_text.strip():
                with st.spinner("Analyzing your journal entries..."):
                    st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text)
            else:
                st.warning("⚠️ Please type a question before asking.")
    with col_right:
        if st.button("🧹 Clear Q&A"):
            st.session_state.question_text = ""
            st.session_state.ai_answer = ""
            st.rerun()

    if st.session_state.ai_answer:
        st.markdown("### 💡 AI Response:")
        st.markdown(
            f"<div style='background-color:#f0f2f6; padding:1rem; border-radius:10px;'>{st.session_state.ai_answer}</div>",
            unsafe_allow_html=True
        )

# -----------------------------
# Tab 2: AI Dialogue
# -----------------------------
with tab_dialogue:
    st.title("💬 AI Dialogue Threads")

    # initialize session state values used by the dialogue tab
    if "dialogue_threads" not in st.session_state:
        st.session_state.dialogue_threads = []  # list of dicts {id,name}
    if "current_thread_id" not in st.session_state:
        st.session_state.current_thread_id = None
    if "current_thread_text" not in st.session_state:
        st.session_state.current_thread_text = ""
    if "dialogue_input" not in st.session_state:
        st.session_state.dialogue_input = ""

    # refresh thread list
    threads = list_threads()
    # build display names for selectbox (strip 'Thread_' prefix and '.txt' suffix)
    thread_labels = ["➕ Start a new thread"] + [t["name"][7:-4] if t["name"].startswith("Thread_") and t["name"].endswith(".txt") else t["name"] for t in threads]

    selection = st.selectbox("Choose a conversation:", thread_labels)

    # Creating a new thread
    if selection == "➕ Start a new thread":
        title = st.text_input("Name this discussion thread:")
        if st.button("Create Thread"):
            if not title.strip():
                st.warning("Please enter a thread name.")
            else:
                try:
                    new_file_id = create_thread_file(title)
                    # store and load
                    st.session_state.current_thread_id = new_file_id
                    st.session_state.current_thread_text = load_thread_by_id(new_file_id)
                    st.session_state.dialogue_input = ""
                    st.success(f"Thread '{title}' created!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to create thread: {e}")

    # Selecting existing thread
    else:
        # locate selected index in threads list
        try:
            idx = thread_labels.index(selection) - 1
            file_id = threads[idx]["id"]
            st.session_state.current_thread_id = file_id
            # load text content from Drive
            st.session_state.current_thread_text = load_thread_by_id(file_id)
        except Exception as e:
            st.error(f"Failed to load selected thread: {e}")

    st.markdown("### Conversation History (editable)")
    # show conversation history as a text area so user can edit and then Save Thread
    st.session_state.current_thread_text = st.text_area(
        "",
        value=st.session_state.current_thread_text,
        height=240,
        placeholder="Conversation history will appear here..."
    )

    st.markdown("---")

    # input for sending one message
    st.markdown("### Send a quick message (this will append & save automatically)")
    st.session_state.dialogue_input = st.text_area(
        "",
        value=st.session_state.dialogue_input,
        height=120,
        placeholder="Write your message to the AI..."
    )

    col_send, col_clear = st.columns([1, 1])
    with col_send:
        if st.button("Send Message"):
            if not st.session_state.current_thread_id:
                st.warning("Please create or select a thread first.")
            elif not st.session_state.dialogue_input.strip():
                st.warning("Please type a message before sending.")
            else:
                file_id = st.session_state.current_thread_id
                # load latest content just before appending to avoid race conditions
                current_text = load_thread_by_id(file_id) or ""
                now_ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                user_msg = st.session_state.dialogue_input.strip()
                current_text += f"\nUser ({now_ts}): {user_msg}\n"

                # Build prompt including current thread and journals
                journal_text = read_all_entries_from_drive()
                prompt = (
                    f"You are an AI assistant in a multi-turn conversation.\n\n"
                    f"Conversation so far:\n{current_text}\n\n"
                    f"Journal context:\n{journal_text}\n\n"
                    f"Respond to the user's latest message naturally and helpfully."
                )

                with st.spinner("AI is generating a reply..."):
                    response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}]
                    )
                    ai_msg = response.choices[0].message.content

                now_ts2 = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                current_text += f"AI ({now_ts2}): {ai_msg}\n"

                # Save appended conversation back to Drive
                save_thread_by_id(file_id, current_text)

                # Update session state so the editable history box shows latest content
                st.session_state.current_thread_text = current_text
                st.session_state.dialogue_input = ""
                st.success("Message sent and thread saved.")
                st.rerun()
    
    with col_clear:
        if st.button("Clear Input"):
            st.session_state.dialogue_input = ""

    # -----------------------------
    # SAVE THREAD button (below conversation history)
    # -----------------------------
    st.markdown("---")
    if st.button("💾 Save Thread"):
        if not st.session_state.current_thread_id:
            st.warning("No thread selected to save.")
        else:
            try:
                # overwrite file with content currently in the conversation history text area
                save_thread_by_id(st.session_state.current_thread_id, st.session_state.current_thread_text)
                st.success("Thread saved to Google Drive.")
            except Exception as e:
                st.error(f"Failed to save thread: {e}")
