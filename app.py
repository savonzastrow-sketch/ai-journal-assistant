import streamlit as st
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
import io
import re
import json

st.set_page_config(page_title="AI Journaling Assistant", layout="centered")

# -----------------------------
# Configuration
# -----------------------------
FOLDER_ID = "0AOJV_s4TPqDcUk9PVA"  # Replace with your Shared Drive folder ID
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# How many recent messages to send to the model (pairs = user+assistant)
RECENT_MESSAGE_PAIRS = 3  # results in ~6 messages (3 user + 3 assistant)
# If you want more context for dialogue, increase RECENT_MESSAGE_PAIRS (tradeoff: tokens)

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
    """Read monthly journal files ONLY (keeps cache)."""
    try:
        query = f"'{FOLDER_ID}' in parents and name contains 'Journal_' and mimeType='text/plain' and trashed=false"
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

def ask_ai_about_entries(question, include_journals=False, last_n_journal_entries=10):
    """Answer based on journal history (optionally include recent journal entries)."""
    try:
        prompt_parts = []
        if include_journals:
            entries_text = read_all_entries_from_drive()
            # keep only recent entries to avoid large prompts if requested
            entries_text = get_recent_entries(entries_text, max_entries=last_n_journal_entries)
            prompt_parts.append("Journal entries (recent):\n" + entries_text)

        prompt_parts.append(f"User question: {question}")
        prompt = "\n\n".join(prompt_parts)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Failed to get AI insights: {e}"

def get_recent_entries(entries_text, max_entries=10):
    """Return only the last N journal entries split by our '---' separator."""
    parts = [p.strip() for p in re.split(r"\n---\n", entries_text) if p.strip()]
    if len(parts) <= max_entries:
        return "\n---\n".join(parts)
    return "\n---\n".join(parts[-max_entries:])

# -----------------------------
# Dialogue helpers (optimized)
# -----------------------------
def list_threads():
    """Return list of files named Thread_*.txt in the folder (as dicts)."""
    try:
        q = f"'{FOLDER_ID}' in parents and name contains 'Thread_' and trashed=false"
        res = drive_service.files().list(q=q, fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        return sorted(files, key=lambda x: x["name"].lower())
    except Exception as e:
        st.error(f"⚠️ Failed to list threads: {e}")
        return []

def create_thread_file(title):
    """Create Thread_<safe_title>.txt with header and return id."""
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
    """Return thread file content string."""
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
    """Overwrite thread content by file id."""
    try:
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
        drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
    except Exception as e:
        st.error(f"⚠️ Failed to save thread (fileId={file_id}): {e}")

# New helper: parse a thread text into a list of ordered message dicts
def parse_thread_text_to_messages(thread_text):
    """
    Parse a thread text like:
      User (timestamp): ...
      AI (timestamp): ...
    into a list of dicts: [{'role':'user','content':...}, {'role':'assistant','content':...}, ...]
    """
    messages = []
    if not thread_text:
        return messages
    # split by lines and group into messages
    lines = thread_text.splitlines()
    buffer_role = None
    buffer_lines = []
    for line in lines:
        # detect prefixes "User (" or "AI (" or "User:" or "AI:"
        m = re.match(r'^(User|AI)\b', line)
        if m:
            # flush previous
            if buffer_role and buffer_lines:
                messages.append({"role": "user" if buffer_role == "User" else "assistant", "content": "\n".join(buffer_lines).strip()})
            buffer_role = m.group(1)
            buffer_lines = [line[len(m.group(1)):].strip(" :\t")]  # take rest
        else:
            buffer_lines.append(line)
    # flush last
    if buffer_role and buffer_lines:
        messages.append({"role": "user" if buffer_role == "User" else "assistant", "content": "\n".join(buffer_lines).strip()})
    return messages

def trim_messages_for_prompt(all_messages, max_pairs=RECENT_MESSAGE_PAIRS):
    """
    Given a list of messages [{'role':...,'content':...}], keep only the last max_pairs*2 messages
    (i.e., last max_pairs user+assistant pairs). Ensure order is preserved.
    """
    if not all_messages:
        return []
    # keep last 2*max_pairs messages
    keep = max_pairs * 2
    return all_messages[-keep:]

def build_chat_messages_for_model(recent_messages, include_system=True, include_journal_text=None):
    """
    Convert recent_messages (list of {'role','content'}) into the OpenAI chat message list.
    Optionally include a short system prompt and a journal context message (if provided).
    """
    messages = []
    if include_system:
        messages.append({
            "role": "system",
            "content": "You are an AI assistant helping the user. Answer concisely and base responses on provided context where relevant."
        })
    if include_journal_text:
        messages.append({
            "role": "system",
            "content": f"Journal context (recent):\n{include_journal_text}"
        })
    # append recent conversation messages
    for m in recent_messages:
        messages.append({"role": m["role"], "content": m["content"]})
    return messages

# -----------------------------
# UI with tabs: Journal (unchanged) and optimized AI Dialogue
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
                    # keep default: do not include journals from dialogues to avoid token spikes
                    st.session_state.ai_answer = ask_ai_about_entries(st.session_state.question_text, include_journals=True, last_n_journal_entries=10)
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
# Tab 2: AI Dialogue (optimized conversation handling)
# -----------------------------
with tab_dialogue:
    st.title("💬 AI Dialogue Threads (efficient)")

    # initialize session state values used by the dialogue tab
    if "dialogue_threads" not in st.session_state:
        st.session_state.dialogue_threads = []  # list of dicts {id,name}
    if "current_thread_id" not in st.session_state:
        st.session_state.current_thread_id = None
    if "current_thread_text" not in st.session_state:
        st.session_state.current_thread_text = ""
    if "dialogue_input" not in st.session_state:
        st.session_state.dialogue_input = ""
    if "include_journal_context_in_dialogue" not in st.session_state:
        st.session_state.include_journal_context_in_dialogue = False

    # refresh thread list
    threads = list_threads()
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
                    st.session_state.current_thread_id = new_file_id
                    st.session_state.current_thread_text = load_thread_by_id(new_file_id)
                    st.session_state.dialogue_input = ""
                    st.success(f"Thread '{title}' created!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to create thread: {e}")

    # Selecting existing thread
    else:
        try:
            idx = thread_labels.index(selection) - 1
            file_id = threads[idx]["id"]
            st.session_state.current_thread_id = file_id
            st.session_state.current_thread_text = load_thread_by_id(file_id)
        except Exception as e:
            st.error(f"Failed to load selected thread: {e}")

    st.markdown("### Conversation History (editable)")
    # show conversation history as a text area so user can edit and then Save Thread manually
    st.session_state.current_thread_text = st.text_area(
        "",
        value=st.session_state.current_thread_text,
        height=240,
        placeholder="Conversation history will appear here..."
    )

    st.markdown("---")
    # small UX control: whether to include journal context in the prompt (off by default)
    include_journals_checkbox = st.checkbox("Include recent journal context in the AI prompt (may use many tokens)", value=False)
    st.session_state.include_journal_context_in_dialogue = include_journals_checkbox

    st.markdown("### Send a quick message (this will append a trimmed context & save automatically)")
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
                # load latest content before appending
                current_text = load_thread_by_id(file_id) or ""
                now_ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                user_msg = st.session_state.dialogue_input.strip()
                current_text += f"\nUser ({now_ts}): {user_msg}\n"

                # parse into structured messages and trim
                all_messages = parse_thread_text_to_messages(current_text)
                recent_messages = trim_messages_for_prompt(all_messages, max_pairs=RECENT_MESSAGE_PAIRS)

                # optionally include recent journal context (trimmed)
                journal_context = None
                if st.session_state.include_journal_context_in_dialogue:
                    journal_context = get_recent_entries(read_all_entries_from_drive(), max_entries=5)

                # build chat messages for the API
                chat_messages = build_chat_messages_for_model(recent_messages, include_system=True, include_journal_text=journal_context)

                # append the user's latest text as a user role (if not already included)
                # (recent_messages should already include the new user msg because we parsed from current_text,
                # but ensure final safety)
                if not (chat_messages and chat_messages[-1].get("role") == "user" and user_msg in chat_messages[-1].get("content", "")):
                    chat_messages.append({"role": "user", "content": user_msg})

                # call the model (trimmed)
                try:
                    with st.spinner("AI is generating a reply (trimmed context)..."):
                        response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=chat_messages
                        )
                        ai_msg = response.choices[0].message.content
                except Exception as e:
                    st.error(f"AI request failed: {e}")
                    ai_msg = f"⚠️ AI request failed: {e}"

                now_ts2 = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                current_text += f"AI ({now_ts2}): {ai_msg}\n"

                # Save appended conversation back to Drive
                save_thread_by_id(file_id, current_text)

                # Update session state so the editable history box shows latest content immediately
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
                save_thread_by_id(st.session_state.current_thread_id, st.session_state.current_thread_text)
                st.success("Thread saved to Google Drive.")
            except Exception as e:
                st.error(f"Failed to save thread: {e}")
