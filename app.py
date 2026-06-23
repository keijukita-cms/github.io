import streamlit as st
import pandas as pd
import json
import uuid
import datetime
from io import BytesIO
import base64
from PIL import Image
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

# --- Configuration & Setup ---
st.set_page_config(
    page_title="CMS HUB Modern",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="⚡"
)

# --- Modern Block UI CSS ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Noto Sans JP', sans-serif;
        color: #334155;
    }
    
    /* Global Background */
    .stApp {
        background-color: #f1f5f9;
    }
    
    /* Typography */
    h1, h2, h3 {
        font-weight: 900 !important;
        color: #0f172a;
        letter-spacing: -0.03em;
    }
    
    /* Section Block Styling */
    .section-block {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 0;
        margin-bottom: 32px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        overflow: hidden;
    }
    
    .section-header-bar {
        background-color: #0f172a; /* Dark Navy */
        color: #ffffff;
        padding: 12px 24px;
        font-size: 0.9rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    
    .section-content {
        padding: 24px;
    }

    /* Input Styling Override */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        color: #334155;
        font-weight: 500;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #3b82f6;
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
    }
    
    /* Data Editor Tweaks */
    div[data-testid="stDataEditor"] {
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        overflow: hidden;
    }

    /* Buttons */
    .stButton button {
        border-radius: 8px;
        font-weight: 700;
        transition: all 0.2s;
    }
    
    /* Hide Streamlit menu */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 2rem; max-width: 1000px !important;}
</style>
""", unsafe_allow_html=True)

# --- Backend Managers (Logic Unchanged) ---

class SheetsManager:
    def __init__(self):
        self.scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        self.is_connected = False
        self.demo_data_key = 'offline_meetings_db'

        if self.demo_data_key not in st.session_state:
            st.session_state[self.demo_data_key] = []

        try:
            if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
                self.creds_dict = dict(st.secrets["connections"]["gsheets"])
                if self.creds_dict.get("private_key") == "test": raise ValueError("Dummy credentials")
                self.creds = Credentials.from_service_account_info(self.creds_dict, scopes=self.scope)
                self.client = gspread.authorize(self.creds)
                self.sheet = self.client.open_by_url(st.secrets["connections"]["gsheets"]["spreadsheet_url"]).sheet1
                self.is_connected = True
        except Exception as e:
            self.is_connected = False

    def fetch_all_meetings(self):
        if not self.is_connected:
            return sorted(st.session_state[self.demo_data_key], key=lambda x: x['date'], reverse=True)
        try:
            records = self.sheet.get_all_records()
            meetings = []
            for row in records:
                try: details = json.loads(row.get('json_data', '{}'))
                except: details = {}
                meetings.append({
                    'id': str(row.get('id')),
                    'date': str(row.get('date')),
                    'title': str(row.get('title')),
                    'participants': str(row.get('participants')),
                    **details
                })
            return sorted(meetings, key=lambda x: x['date'], reverse=True)
        except Exception: return []

    def save_meeting(self, meeting_data):
        json_payload = {
            'agendas': meeting_data.get('agendas', []),
            'tasks': meeting_data.get('tasks', []),
            'discussionNotes': meeting_data.get('discussionNotes', ''),
            'rawTranscript': meeting_data.get('rawTranscript', ''),
            'officialMinutes': meeting_data.get('officialMinutes', ''),
            'links': meeting_data.get('links', []),
            'image_descriptions': meeting_data.get('image_descriptions', [])
        }
        
        if not self.is_connected:
            db = st.session_state[self.demo_data_key]
            found = False
            for i, m in enumerate(db):
                if m['id'] == meeting_data['id']:
                    db[i] = meeting_data
                    found = True
                    break
            if not found: db.append(meeting_data)
            return True

        try:
            row_data = [meeting_data['id'], meeting_data['date'], meeting_data['title'], meeting_data['participants'], json.dumps(json_payload, ensure_ascii=False)]
            try:
                cell = self.sheet.find(meeting_data['id'])
                self.sheet.update(f"A{cell.row}:E{cell.row}", [row_data])
            except gspread.exceptions.CellNotFound:
                self.sheet.append_row(row_data)
            return True
        except Exception as e:
            st.error(f"Save Error: {e}")
            return False

    def delete_meeting(self, meeting_id):
        if not self.is_connected:
            st.session_state[self.demo_data_key] = [m for m in st.session_state[self.demo_data_key] if m['id'] != meeting_id]
            return True
        try:
            cell = self.sheet.find(meeting_id)
            self.sheet.delete_rows(cell.row)
            return True
        except: return False

class AIManager:
    def __init__(self):
        self.is_active = False
        try:
            api_key = st.secrets["gemini"]["api_key"]
            if api_key != "test_key":
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                self.is_active = True
        except: pass

    def transcribe(self, audio_bytes):
        if not self.is_active: return "【オフライン】音声認識機能はAPIキー設定後に有効になります。"
        try:
            response = self.model.generate_content(["文字起こしを行ってください。", {"mime_type": "audio/wav", "data": audio_bytes}])
            return response.text
        except Exception as e: return f"Error: {e}"

    def analyze_image(self, image_data):
        if not self.is_active: return "【オフライン】画像解析ダミーテキスト"
        try:
            img = Image.open(image_data)
            response = self.model.generate_content(["この画像に何が写っているか、議事録の文脈で詳細に説明してください。", img])
            return response.text
        except Exception as e: return f"Error: {e}"

    def generate_minutes(self, transcript, metadata, notes, img_desc):
        if not self.is_active: return "## 【オフライン】議事録サンプル\n- APIキーを設定してください。"
        prompt = f"""
        あなたは戦略顧問です。以下の情報を統合し、構造化された議事録を作成してください。
        
        【会議情報】{metadata['date']} / {metadata['title']} / {metadata['participants']}
        【メモ】{notes}
        【画像資料の内容】{img_desc}
        【文字起こし】{transcript}
        
        出力はマークダウン形式で、決定事項とネクストアクションを明確にしてください。
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e: return f"Error: {e}"

# --- Init ---
if 'db' not in st.session_state: st.session_state.db = SheetsManager()
if 'ai' not in st.session_state: st.session_state.ai = AIManager()

# --- Logic ---
def load_data():
    st.session_state.meetings = st.session_state.db.fetch_all_meetings()

def create_new():
    new_id = str(uuid.uuid4())
    today = datetime.date.today().strftime('%Y-%m-%d')
    new_meeting = {
        'id': new_id, 'date': today, 'title': 'New Strategic Meeting', 'participants': '',
        'agendas': [{'Topic': 'Opening', 'Duration': 10}], 'tasks': [],
        'discussionNotes': '', 'rawTranscript': '', 'officialMinutes': '',
        'links': [], 'image_descriptions': []
    }
    st.session_state.db.save_meeting(new_meeting)
    st.session_state.active_id = new_id
    load_data()
    st.rerun()

# --- App UI ---

# Sidebar
with st.sidebar:
    st.markdown("### CMS HUB <span style='font-size:0.8em; color:gray'>v7.5</span>", unsafe_allow_html=True)
    if st.button("＋ Create New Meeting", type="primary", use_container_width=True): create_new()
    st.markdown("---")
    
    if 'meetings' not in st.session_state: load_data()
    
    if st.session_state.meetings:
        selected_id = st.radio(
            "HISTORY",
            options=[m['id'] for m in st.session_state.meetings],
            format_func=lambda x: next((f"{m['date']} | {m['title']}" for m in st.session_state.meetings if m['id'] == x), "Unknown"),
            index=0 if 'active_id' not in st.session_state or st.session_state.active_id not in [m['id'] for m in st.session_state.meetings] else [m['id'] for m in st.session_state.meetings].index(st.session_state.active_id)
        )
        if selected_id != st.session_state.get('active_id'):
            st.session_state.active_id = selected_id
            st.rerun()
    else:
        st.info("No meetings found.")

# Main Content
if st.session_state.get('active_id'):
    current_meeting = next((m for m in st.session_state.meetings if m['id'] == st.session_state.active_id), None)
    
    if current_meeting:
        # Header Area
        col_h1, col_h2 = st.columns([5, 1])
        with col_h1:
            st.title(current_meeting['title'])
            st.markdown(f"**DATE:** `{current_meeting['date']}` &nbsp; **PARTICIPANTS:** `{current_meeting['participants']}`")
        with col_h2:
            if st.button("🗑️", help="Delete this meeting"):
                if st.session_state.db.delete_meeting(current_meeting['id']):
                    st.toast("Deleted")
                    load_data()
                    st.session_state.active_id = None
                    st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # --- MAIN FORM (Starts Here) ---
        with st.form("main_form", clear_on_submit=False):
            
            # --- BLOCK 0: BASIC INFO ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">📝 Basic Info</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            c1, c2, c3 = st.columns([1, 2, 2])
            new_date = c1.text_input("Date", value=current_meeting.get('date', ''))
            new_title = c2.text_input("Title", value=current_meeting.get('title', ''))
            new_part = c3.text_input("Participants", value=current_meeting.get('participants', ''))
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- BLOCK 1: AGENDA ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">1. Agenda</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            
            agendas_df = pd.DataFrame(current_meeting.get('agendas', []))
            if agendas_df.empty: agendas_df = pd.DataFrame(columns=['Topic', 'Duration'])
            
            edited_agendas = st.data_editor(
                agendas_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Topic": st.column_config.TextColumn("Topic", width="large", required=True),
                    "Duration": st.column_config.NumberColumn("Min", width="small", min_value=1, step=5)
                },
                key="agenda_editor"
            )
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- BLOCK 2: ACTION ITEMS ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">2. Action Items</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            
            tasks_df = pd.DataFrame(current_meeting.get('tasks', []))
            if tasks_df.empty: tasks_df = pd.DataFrame(columns=['Done', 'Task', 'Assignee', 'Deadline'])
            
            edited_tasks = st.data_editor(
                tasks_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Done": st.column_config.CheckboxColumn("Done", width="small", default=False),
                    "Task": st.column_config.TextColumn("Task", width="large", required=True),
                    "Assignee": st.column_config.TextColumn("Who", width="medium"),
                    "Deadline": st.column_config.DateColumn("Due", width="medium")
                },
                key="task_editor"
            )
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- BLOCK 3: DISCUSSION & LINKS ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">3. Discussion & Assets</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            
            st.caption("Discussion Notes")
            new_notes = st.text_area("Notes", value=current_meeting.get('discussionNotes', ''), height=200, label_visibility="collapsed")
            
            st.markdown("---")
            st.caption("Reference Links")
            links_df = pd.DataFrame(current_meeting.get('links', []))
            if links_df.empty: links_df = pd.DataFrame(columns=['Title', 'URL'])
            edited_links = st.data_editor(
                links_df, num_rows="dynamic", use_container_width=True,
                column_config={
                    "Title": st.column_config.TextColumn("Link Title"),
                    "URL": st.column_config.LinkColumn("URL")
                },
                key="links_editor"
            )
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- BLOCK 4: RAW TRANSCRIPT ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">4. Transcript (AI)</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            st.caption("AIによって生成された文字起こしデータ（編集可能）")
            new_transcript = st.text_area("Transcript", value=current_meeting.get('rawTranscript', ''), height=200, label_visibility="collapsed")
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- BLOCK 5: OFFICIAL MINUTES ---
            st.markdown('<div class="section-block">', unsafe_allow_html=True)
            st.markdown('<div class="section-header-bar">5. Official Minutes</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-content">', unsafe_allow_html=True)
            st.caption("最終的な議事録（AI生成 + 手動編集）")
            new_minutes = st.text_area("Minutes", value=current_meeting.get('officialMinutes', ''), height=400, label_visibility="collapsed")
            st.markdown('</div></div>', unsafe_allow_html=True)

            # --- SAVE ACTIONS ---
            st.markdown("### Actions")
            submitted = st.form_submit_button("💾 Save All Changes", type="primary", use_container_width=True)

            if submitted:
                updated_data = {
                    'id': current_meeting['id'],
                    'date': new_date,
                    'title': new_title,
                    'participants': new_part,
                    'agendas': edited_agendas.to_dict('records'),
                    'tasks': edited_tasks.to_dict('records'),
                    'discussionNotes': new_notes,
                    'links': edited_links.to_dict('records'),
                    'rawTranscript': new_transcript,
                    'officialMinutes': new_minutes,
                    'image_descriptions': current_meeting.get('image_descriptions', []) 
                }
                st.session_state.db.save_meeting(updated_data)
                st.toast("Saved successfully!", icon="✅")
                st.rerun()

        # --- AI Tools (Outside Form) ---
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### ⚡ AI Tools")
        
        c_ai1, c_ai2, c_ai3 = st.columns(3)
        
        with c_ai1:
            with st.expander("🎙️ Transcribe Audio"):
                audio_file = st.file_uploader("Upload Audio", type=['wav', 'mp3', 'm4a'])
                if audio_file and st.button("Start Transcription"):
                    with st.spinner("Processing..."):
                        ts = st.session_state.ai.transcribe(audio_file.read())
                        current_meeting['rawTranscript'] = ts
                        st.session_state.db.save_meeting(current_meeting)
                        st.success("Done! Reload required.")
                        st.rerun()

        with c_ai2:
            with st.expander("🖼️ Analyze Image"):
                img_file = st.file_uploader("Upload Image", type=['png', 'jpg', 'jpeg'])
                if img_file and st.button("Analyze Context"):
                    with st.spinner("Analyzing..."):
                        desc = st.session_state.ai.analyze_image(img_file)
                        current_descriptions = current_meeting.get('image_descriptions', [])
                        current_descriptions.append(f"[Image: {img_file.name}] {desc}")
                        current_meeting['image_descriptions'] = current_descriptions
                        st.session_state.db.save_meeting(current_meeting)
                        st.success("Added to context!")
                        st.rerun()

        with c_ai3:
            st.info("Transcript & Notes required")
            if st.button("✨ Generate Minutes", use_container_width=True):
                with st.spinner("Generating..."):
                    img_context = "\n".join(current_meeting.get('image_descriptions', []))
                    mins = st.session_state.ai.generate_minutes(
                        current_meeting.get('rawTranscript', ''),
                        current_meeting,
                        current_meeting.get('discussionNotes', ''),
                        img_context
                    )
                    current_meeting['officialMinutes'] = mins
                    st.session_state.db.save_meeting(current_meeting)
                    st.rerun()

else:
    st.write("Initializing...")