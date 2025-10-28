import streamlit as st
from datetime import datetime
import os
from openai import OpenAI

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

SAVE_PATH = "Journals"
os.makedirs(SAVE_PATH, exist_ok=True)

st.set_page_config(page_title="AI Journal", layout="centered")
st.title("🌿 AI Journaling Assistant")

tab1, tab2 = st.tabs(["✍️ New Entry", "🧠 Journal Insights"])

with tab1:
    st.subheader("Write your thoughts")
    entry = st.text_area("Today's reflection:", height=300, placeholder="How are you feeling today?")
    if st.button("Save Entry"):
        if entry.strip():
            filename = f"Journal_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.txt"
            with open(os.path.join(SAVE_PATH, filename), "w") as f:
                f.write(entry.strip())
            st.success(f"Saved journal entry: {filename}")
        else:
            st.warning("Please write something before saving.")

with tab2:
    st.subheader("Ask your journals a question")
    question = st.text_input("What would you like to know?")
    if st.button("Ask AI"):
        journal_texts = []
        for filename in sorted(os.listdir(SAVE_PATH)):
            if filename.endswith(".txt"):
                with open(os.path.join(SAVE_PATH, filename), "r") as f:
                    journal_texts.append(f"--- {filename} ---\n{f.read()}")

        all_journals = "\n\n".join(journal_texts)

        if not all_journals:
            st.warning("No journal entries found yet.")
        elif question.strip():
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You analyze a person’s journal entries and provide factual, reflective insights when asked."},
                    {"role": "user", "content": f"Here are my journals:\n{all_journals}\n\nQuestion: {question}"}
                ]
            )
            st.text_area("💬 AI Insight:", value=response.choices[0].message.content.strip(), height=300)
        else:
            st.warning("Please enter a question.")
