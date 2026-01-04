import streamlit as st
import pandas as pd
import altair as alt
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2 import service_account
import io
import re
from datetime import datetime, timedelta

# --- GOOGLE DRIVE SETUP ---
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'service_account.json'

def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds)

# --- DATA PARSING LOGIC ---
def get_last_30_days_data():
    service = get_drive_service()
    # Find your journal file
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
    lines = content.split('\n')
    
    data = []
    current_date = None
    
    # Updated parsing to handle multiple exercises and default values
    for line in lines:
        if "🗓️" in line:
            # Extract date: "January 01, 2026"
            date_str = line.split("🗓️")[1].split("at")[0].strip()
            try:
                current_date = datetime.strptime(date_str, "%B %d, %Y").date()
            except:
                continue
                
        if current_date and "DAILY TEMPLATE SUMMARY:" in line:
            # Initialize entry with "None" to solve the Yoga mystery
            entry = {
                "Date": current_date,
                "Satisfaction": 0,
                "Neuralgia": 0,
                "Exercise_Type": "None",
                "Exercise_Mins": 0.0
            }
            
            # Sub-loop to find metrics for this specific day
            idx = lines.index(line)
            for sub_line in lines[idx:idx+15]:
                if "Satisfaction:" in sub_line:
                    try: entry["Satisfaction"] = int(sub_line.split(":")[1].split("/")[0])
                    except: pass
                if "Neuralgia:" in sub_line:
                    try: entry["Neuralgia"] = int(sub_line.split(":")[1].split("/")[0])
                    except: pass
                if "Exercise:" in sub_line:
                    # Logic for "Swim (33 mins)"
                    ex_part = sub_line.split(":")[1].strip()
                    if "(" in ex_part:
                        entry["Exercise_Type"] = ex_part.split("(")[0].strip()
                        try:
                            mins_val = ex_part.split("(")[1].split(" ")[0]
                            entry["Exercise_Mins"] = float(mins_val)
                        except:
                            entry["Exercise_Mins"] = 0.0
                    else:
                        entry["Exercise_Type"] = ex_part
            
            data.append(entry)
            
    return pd.DataFrame(data)

# --- MAIN APP ---
st.title("AI Journal Assistant")

# Fetch Data
df_metrics = get_last_30_days_data()

if not df_metrics.empty:
    # 1. Clean and Format
    df_plot = df_metrics.dropna(subset=['Satisfaction', 'Neuralgia']).copy()
    df_plot['Date'] = pd.to_datetime(df_plot['Date'])
    df_plot = df_plot.sort_values(['Date', 'Exercise_Type'])

    st.subheader("Health & Exercise Trends")

    # 2. Build Chart
    base = alt.Chart(df_plot).encode(
        x=alt.X('yearmonthdate(Date):T', title='Date', axis=alt.Axis(format='%b %d'))
    )

    # Bars (Stacked by Activity)
    bars = base.mark_bar(opacity=0.6, xOffset=-15).encode(
        y=alt.Y('Exercise_Mins:Q', title='Exercise (Mins)'),
        color=alt.Color('Exercise_Type:N', title="Activity",
            scale=alt.Scale(domain=['Swim', 'Run', 'Cycle', 'Yoga', 'Other'],
                          range=['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#7f7f7f'])
        ),
        tooltip=['Date:T', 'Exercise_Type:N', 'Exercise_Mins:Q']
    )

    # Lines (Health Metrics)
    lines = alt.Chart(df_plot).transform_fold(
        ['Satisfaction', 'Neuralgia'], as_=['Metric', 'Rating']
    ).mark_line(point=True).encode(
        x='yearmonthdate(Date):T',
        y=alt.Y('Rating:Q', title='Rating (1-5)', scale=alt.Scale(domain=[1, 5])),
        color=alt.Color('Metric:N', scale=alt.Scale(range=['#636EFA', '#EF553B'])),
        tooltip=['Date:T', 'Metric:N', 'Rating:Q']
    )

    # Combine
    st.altair_chart(alt.layer(bars, lines).resolve_scale(y='independent').properties(height=400), use_container_width=True)

else:
    st.info("No data found. Please log an entry in the Daily Template.")

# --- SAVE LOGIC (Protected) ---
def save_entry_to_drive(new_text):
    service = get_drive_service()
    # 1. Get existing content first
    # [Rest of your Drive save logic here, ensuring current_content is appended to new_text]
