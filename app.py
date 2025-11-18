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
FOLDER_ID = "0AOJV_s4TPqDcUk9PVA"  # Shared Drive folder ID
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
    DELEGATED_EMAIL = "stefan@zeitadvisory.com"  # Workspace email

    creds = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=SCOPES
    )
    delegated_creds = creds.with_subject(DELEGATED_EMAIL)

    service = build("drive", "v3", credentials=delegated_creds)
    return service

drive_service = get_drive_service()

# -----------------------------
# JOURNAL HELPERS
# -----------------------------
def get_or_create_monthly_file():
    """Find or create a monthly journal file."""
    now = datetime.now(ZoneInfo("America/New_York"))
    month_file_name = f"Journal_{now.strftime('%Y-%m')}.txt"

    query = (
        f"'{FOLDER_ID}' in parents and name='{month_file_name}' and mimeType='text/plain'"
    )
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create file if not found
    file_metadata = {"name": month_file_name, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain")
    file = drive_service.files().create(
        body=file_metadata, media_body=media, supportsAllDrives=True
    ).execute()
    return file["id"]


def append_entry_to_monthly_file(entry_text):
    """Append a journal entry."""
    try:
        file_id = get_or_create_monthly_file()

        # Read current file
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        existing = fh.read().decode("utf-8")

        # Format entry
        now = datetime.now(ZoneInfo("America/New_York"))
        new_entry = (
            f"\n\n---\n🗓️ {now.strftime('%B %d, %Y %I:%M %p EST')}\n"
            f"{entry_text.strip()}\n"
        )

        updated = existing + new_entry

        # Upload back
        media = MediaIoBaseUpload(
            io.BytesIO(updated.encode("utf-8")), mimetype="text/plain"
        )
        drive_service.files().update(
            fileId=file_id, media_body=media, supportsAllDrives=True
        ).execute()

        return True, f"✅ Entry saved to {now.strftime('%B %Y')} journal!"

    except Exception as e:
        return False, f"⚠️ Failed to save entry: {e}"


@st.cache_data(ttl=300)
def read_all_entries_from_drive():
    """Fetch all journal files."""
    try:
        query = f"'{FOLDER_ID}' in parents and mimeType='text/plain'"
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = sorted(
            [f for f in results.get("files", []) if f["name"].startswith("Journal_")],
            key=lambda x: x["name"]
        )

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
    """Answer based on journal history."""
    try:
        entries_text = read_all_entries_from_drive()
        if not entries_text.strip():
            return "No journal entries available yet."

        prompt = (
            f"You are an AI journaling assistant. The user has provided these journal entries:\n\n"
            f"{entries_text}\n\n"
            f"User question: {question}\n"
            f"Answer concisely based ONLY on journal content."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"⚠️ Failed to get AI insights: {e}"


# -----------------------------
# DIALOGUE THREAD HELPERS
# -----------------------------
def list_threads():
    """Return all files starting with Thread_."""
    try:
        query = f"'{FOLDER_ID}' in parents and name contains 'Thread_'"
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        return sorted(results.get("files", []), key=lambda x: x["name"])

    except Exception as e:
        st.error(f"⚠️ Failed to list threads: {e}")
        return []


def load_thread(file_id):
    """Read a thread file."""
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
        return f"⚠️ Failed to load thread: {e}"


def save_thread(file_id, content):
    """Overwrite thread file."""
    try:
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype="text/plain"
        )
        drive_service.files().update(
            fileId=file_id, media_body=media, supportsAllDrives=True
        ).execute()
    except Exception as e:
        st.error(f"⚠️ Failed to save thread: {e}")


def create_thread_file(title):
    safe_title = re.sub(r"[^a-zA-Z0-9_]+", "_", title)
    filename = f"Thread_{safe_title}.txt"

    metadata = {"name": filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain")
    file = drive_service.files().create(
        body=metadata, media_body=media, supportsAllDrives=True
    ).execute()
    return file["id"]


# -----------------------------
# STREAMLIT UI – TABS
# -----------------------------
tab1, tab2 = st.tabs(["📝 Journal", "💬 AI Dialogue"])


# ==========================================================================
# TAB 1 — JOURNAL
# ==========================================================================
with tab1:
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
                    st.session_state.ai_answer = ask_ai_about_entries(
                        st.session_state.question_text
                    )
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


# ==========================================================================
# TAB 2 — AI DIALOGUE THREADS
# ==========================================================================
with tab2:
    st.title("💬 AI Dialogue Threads")

    threads = list_threads()
    thread_names = ["➕ Start a new thread"] + [t["name"][7:-4] for t in threads]

    selection = st.selectbox("Choose a conversation:", thread_names)

    if "current_thread_id" not in st.session_state:
        st.session_state.current_thread_id = None
    if "current_thread_text" not in st.session_state:
        st.session_state.current_thread_text = ""
    if "dialogue_input" not in st.session_state:
        st.session_state.dialogue_input = ""

    # NEW THREAD
    if selection == "➕ Start a new thread":
        title = st.text_input("Name this discussion thread:")
        if st.button("Create Thread"):
            if not title.strip():
                st.warning("Please enter a thread name.")
            else:
                file_id = create_thread_file(title)
                st.session_state.current_thread_id = file_id
                st.session_state.current_thread_text = ""
                st.success(f"Thread '{title}' created!")
                st.rerun()

    # EXISTING THREAD
    else:
        idx = thread_names.index(selection) - 1
        file_id = threads[idx]["id"]

        st.session_state.current_thread_id = file_id
        content = load_thread(file_id)
        st.session_state.current_thread_text = content

        st.markdown("### Conversation History")
        st.markdown(
            f"<div style='background-color:#f0f2f6; padding:1rem; height:200px; overflow-y:auto; white-space:pre-wrap; border-radius:10px;'>"
            f"{content}</div>",
            unsafe_allow_html=True
        )

        st.markdown("### Your Message")
        st.session_state.dialogue_input = st.text_area(
            "",
            value=st.session_state.dialogue_input,
            height=120,
            placeholder="Write your message to the AI..."
        )

        if st.button("Send Message"):
            if st.session_state.dialogue_input.strip():
                user_msg = st.session_state.dialogue_input.strip()
                updated = (
                    st.session_state.current_thread_text
                    + f"\nUser: {user_msg}\n"
                )

                # Create prompt using thread + journal entries as context
                journal_text = read_all_entries_from_drive()
                prompt = (
                    f"You are an AI assistant engaged in an ongoing discussion.\n"
                    f"Here is the conversation so far:\n\n{updated}\n\n"
                    f"Here are the user's journal entries for helpful context:\n\n{journal_text}\n\n"
                    f"Respond to the user's latest message naturally and helpfully."
                )

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}]
                )
                ai_msg = response.choices[0].message.content

                updated += f"AI: {ai_msg}\n"

                save_thread(file_id, updated)

                st.session_state.dialogue_input = ""
                st.rerun()
            else:
                st.warning("Please enter a message before sending.")
