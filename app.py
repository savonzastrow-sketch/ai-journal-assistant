import streamlit as st
import pandas as pd
import altair as alt
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2 import service_account
import io
import re
from datetime import datetime, timedelta

# --- 1. SETTINGS & AUTH ---
st.set_page_config(page_title="AI Journal Assistant", layout="wide")

# This uses your existing secrets setup
def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

# --- 2. DATA EXTRACTION ---
def get_last_30_days_data():
    service = get_drive_service()
    results = service.files().list(q="name='Journal.txt'", fields="files(id, name)").execute()
    items = results.get('files', [])
    if not items: return pd.DataFrame()
    
    file_id = items[0]['id']
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    
    content = fh.getvalue().decode('utf-8')
    sections = content.split('---') # Split by your entry separator
    
    data = []
    for section in sections:
        if "DAILY TEMPLATE SUMMARY:" in section:
            entry = {"Satisfaction": 0, "Neuralgia": 0, "Exercise_Type": "None", "Exercise_Mins": 0.0}
            
            # Extract Date
            date_match = re.search(r"🗓️\s*([A-Z][a-z]+ \d{1,2}, \d{4})", section)
            if date_match:
                try:
                    entry["Date"] = datetime.strptime(date_match.group(1), "%B %d, %Y").date()
                except: continue
            else: continue

            # Extract Metrics
            lines = section.split('\n')
            for line in lines:
                if "Satisfaction:" in line:
                    entry["Satisfaction"] = int(re.search(r"(\d)", line).group(1)) if re.search(r"(\d)", line) else 0
                elif "Neuralgia:" in line:
                    entry["Neuralgia"] = int(re.search(r"(\d)", line).group(1)) if re.search(r"(\d)", line) else 0
                elif "Exercise:" in line:
                    ex_content = line.split("Exercise:")[1].strip()
                    if "(" in ex_content:
                        entry["Exercise_Type"] = ex_content.split("(")[0].strip()
                        mins_match = re.search(r"(\d+\.?\d*)", ex_content.split("(")[1])
                        entry["Exercise_Mins"] = float(mins_match.group(1)) if mins_match else 0.0
                    else:
                        entry["Exercise_Type"] = ex_content
            data.append(entry)
            
    return pd.DataFrame(data)

# --- 3. UI & VISUALIZATION ---
st.title("AI Journal Assistant")

# Fetch Data
df_metrics = get_last_30_days_data()

if not df_metrics.empty:
    # CRITICAL: Fix for the date overlap and sorting
    df_plot = df_metrics.dropna(subset=['Satisfaction', 'Neuralgia']).copy()
    df_plot['Date'] = pd.to_datetime(df_plot['Date'])
    df_plot = df_plot.sort_values(['Date', 'Exercise_Type'])

    st.subheader("Health & Exercise Trends")

    # Base Chart
    base = alt.Chart(df_plot).encode(
        x=alt.X('yearmonthdate(Date):T', title='Date', axis=alt.Axis(format='%b %d'))
    )

    # Bars for Exercise (Stacked)
    bars = base.mark_bar(opacity=0.6, xOffset=-15).encode(
        y=alt.Y('Exercise_Mins:Q', title='Exercise (Mins)'),
        color=alt.Color('Exercise_Type:N', title="Activity",
            scale=alt.Scale(domain=['Swim', 'Run', 'Cycle', 'Yoga', 'Other'],
                          range=['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#7f7f7f'])
        ),
        tooltip=['Date:T', 'Exercise_Type:N', 'Exercise_Mins:Q']
    )

    # Lines for Ratings
    lines = alt.Chart(df_plot).transform_fold(
        ['Satisfaction', 'Neuralgia'], as_=['Metric', 'Rating']
    ).mark_line(point=True, size=3).encode(
        x='yearmonthdate(Date):T',
        y=alt.Y('Rating:Q', title='Rating (1-5)', scale=alt.Scale(domain=[1, 5])),
        color=alt.Color('Metric:N', scale=alt.Scale(range=['#636EFA', '#EF553B'])),
        tooltip=['Date:T', 'Metric:N', 'Rating:Q']
    )

    combined = alt.layer(bars, lines).resolve_scale(y='independent').properties(height=450)
    st.altair_chart(combined, use_container_width=True)
else:
    st.info("No data found. Ensure your Journal.txt contains the 'DAILY TEMPLATE SUMMARY:' section.")

# --- 4. TEMPLATE SECTION ---
# (Keep your existing tab logic and save_entry_to_drive function here)
st.divider()
st.write("Use the Daily Template to log new entries.")
