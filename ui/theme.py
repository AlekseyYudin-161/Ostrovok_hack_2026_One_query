"""Тема «О!дин запрос»: цвета, CSS и загрузка фото. Единственное место со стилями.

Стиль: сайт Островка (синий #0D41D2, салатовый #CDF4B0 только акцентами) + iOS-минимализм
и «стеклянные» карточки. Серые оттенки: #F2F2F7 фон, #E9E9EE/#E5E5EA/#D1D1D6 линии и плашки,
#86868B/#6C6C70/#3A3A3C текст.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

BLUE = "#0D41D2"
GREEN = "#CDF4B0"          # светло-салатовый: только акцент
GREEN_TEXT = "#1E4A12"
BLUE_HOVER = "#0B38B8"
BLUE_PRESSED = "#082C94"
BLUE_TINT = "#E8EEFD"
RING = "rgba(13,65,210,.28)"
TEXT = "#14161F"

ASSETS = Path(__file__).resolve().parent / "assets" / "photos"


@lru_cache(maxsize=32)
def photo_uri(name: str) -> str:
    """Фото из ui/assets/photos как data-URI (в HTML-карточки Streamlit иначе не вставить)."""
    path = ASSETS / f"{name}.jpg"
    if not path.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp {{ font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Inter',system-ui,sans-serif; color:{TEXT}; }}
.stApp {{ background:#F5F5F7; }}
footer, #MainMenu, [data-testid="stToolbar"] {{ display:none !important; }}
/* шапку Streamlit не прячем: в ней кнопка раскрытия бокового меню */
header[data-testid="stHeader"] {{ background:transparent; }}
.block-container {{ max-width:1040px; padding:20px 24px 96px; }}
[data-testid="stVerticalBlock"] {{ gap:1rem !important; }}
/* Streamlit гасит нижний отступ markdown отрицательным margin (под последний <p>); у нашего HTML <p> нет,
   поэтому отступ между блоками «съедался». Обнуляем, и расстояния задаёт только gap. */
[data-testid="stMarkdown"], [data-testid="stMarkdownContainer"], .stMarkdown {{ margin-bottom:0 !important; }}
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"]) {{ margin-bottom:0 !important; }}

/* карточки */
.od-glass {{ background:#fff; border:1px solid #E8E8ED; box-shadow:0 1px 2px rgba(20,30,60,.04),0 8px 24px rgba(20,30,60,.04);
  border-radius:24px; padding:24px; transition:box-shadow .15s, border-color .15s; }}
.od-glass.tight {{ padding:10px; }}
.od-grid3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; align-items:stretch; }}
@media (max-width:760px) {{ .od-grid3 {{ grid-template-columns:1fr; }} }}
.od-glass.rec {{ border:2px solid {GREEN}; }}
.od-glass.dim {{ opacity:.7; }}
.od-head {{ background:#fff; border:1px solid #E8E8ED;
  border-radius:24px; padding:12px 14px 12px 24px; margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; gap:16px; }}
.od-logo {{ font-weight:700; font-size:21px; letter-spacing:-.4px; color:{TEXT}; }}
.od-logo b {{ color:{BLUE}; }}
.od-h1 {{ margin:0; font-size:56px; line-height:1.05; font-weight:700; letter-spacing:-2px; text-align:center; }}
.od-h2 {{ margin:0; font-size:30px; font-weight:700; letter-spacing:-1px; }}
.od-h3 {{ margin:0; font-size:20px; font-weight:700; letter-spacing:-.3px; }}
.od-muted {{ margin:0; font-size:15px; line-height:1.5; color:#6C6C70; }}
.od-small {{ font-size:13px; color:#6C6C70; }}
.od-mark {{ background:{GREEN}; padding:0 .15em; border-radius:.2em; }}
.od-blue {{ color:{BLUE}; }}

/* чипы */
.od-chip {{ display:inline-block; background:#F2F2F7; color:#3A3A3C; font-size:13px; font-weight:600; padding:6px 12px; border-radius:999px; white-space:nowrap; margin:0 8px 8px 0; }}
.od-chip.dash {{ background:transparent; border:1.5px dashed #AEAEB2; }}
.od-chip.blue {{ background:#E8EEFD; color:{BLUE}; }}
.od-chip.solid {{ background:{BLUE}; color:#fff; }}
.od-chip.green {{ background:{GREEN}; color:{GREEN_TEXT}; }}
.od-chip.red {{ background:#FDECEA; color:#B3261E; }}
.od-chip.white {{ background:#fff; border:1px solid #E5E5EA; }}

/* степпер */
.od-steps {{ display:flex; align-items:center; gap:12px; padding:0 8px; margin:8px 0 16px; }}
.od-step {{ display:flex; align-items:center; gap:8px; font-size:14px; font-weight:600; color:#86868B; }}
.od-step i {{ width:26px; height:26px; border-radius:50%; background:#E9E9EE; color:#86868B; font-style:normal; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:700; }}
.od-step.now {{ color:{TEXT}; }} .od-step.now i {{ background:{BLUE}; color:#fff; }}
.od-step.done {{ color:{TEXT}; }} .od-step.done i {{ background:transparent; color:{GREEN_TEXT}; }}
.od-line {{ flex:1; height:2px; min-width:20px; background:#E5E5EA; border-radius:2px; }}

/* фото и строки */
.od-cover {{ position:relative; height:170px; border-radius:14px; overflow:hidden; background:#E5E5EA; }}
.od-cover img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.od-badge {{ position:absolute; top:12px; left:12px; background:rgba(255,255,255,.92); color:#14161F; font-size:12px; font-weight:700; padding:5px 11px; border-radius:999px; }}
.od-badge.green {{ background:{GREEN}; color:{GREEN_TEXT}; }}
.od-badge.right {{ left:auto; right:12px; }}
.od-row {{ display:flex; justify-content:space-between; gap:16px; font-size:15px; }}
.od-row span:first-child {{ color:#6C6C70; }}
.od-row.big {{ font-size:20px; font-weight:700; }} .od-row.big span:first-child {{ color:{TEXT}; }}
.od-stats {{ display:grid; grid-template-columns:1fr 1fr; gap:12px 10px; padding:12px 0; border-top:1px solid #E5E5EA; border-bottom:1px solid #E5E5EA; }}
.od-stats small {{ display:block; font-size:11px; color:#86868B; text-transform:uppercase; letter-spacing:.4px; font-weight:600; }}
.od-stats b {{ font-size:15px; }}
.od-price {{ font-size:22px; font-weight:700; letter-spacing:-.5px; }}
.od-leg {{ display:flex; gap:16px; align-items:flex-start; padding:16px 0; border-bottom:1px solid #EEEEF2; }}
.od-leg .t {{ font-size:17px; font-weight:600; }} .od-leg .s {{ font-size:14px; color:#6C6C70; line-height:1.4; }}
.od-leg .p {{ margin-left:auto; font-size:16px; font-weight:600; white-space:nowrap; }}
.od-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin:6px 12px 0 0; flex:0 0 auto; }}
.od-li {{ display:flex; align-items:flex-start; font-size:16px; line-height:1.45; margin:0 0 12px; }}
.od-tl {{ display:flex; gap:16px; align-items:flex-start; margin:0 0 20px; }}
.od-tl .t {{ font-size:17px; font-weight:600; }} .od-tl .s {{ font-size:14px; color:#6C6C70; }}
.od-tl .todo .t {{ color:#86868B; }}
.od-ring {{ flex:0 0 auto; width:26px; height:26px; border-radius:50%; border:2px solid #D1D1D6; }}
.od-ring.run {{ border:3px solid #E5E5EA; border-top-color:{BLUE}; animation:od-spin 1s linear infinite; }}
.od-ring.ok {{ border:0; width:26px; height:26px; color:{GREEN_TEXT}; display:flex; align-items:center; justify-content:center; }}
.od-ring.ok svg {{ width:20px; height:20px; }}
@keyframes od-spin {{ to {{ transform:rotate(360deg); }} }}
.od-bar {{ height:8px; border-radius:8px; background:#E5E5EA; overflow:hidden; }}
.od-bar > div {{ height:100%; border-radius:8px; background:linear-gradient(90deg,{BLUE},#5B85F0); transition:width .3s; }}
.od-bubble-user {{ margin-left:auto; max-width:620px; background:{BLUE}; color:#fff; border-radius:24px 24px 6px 24px; padding:16px 20px; font-size:16px; line-height:1.45; width:fit-content; }}
.od-cta {{ background:linear-gradient(135deg,#2A5FEA 0%,{BLUE} 55%,#0A2F9E 100%); border-radius:24px; padding:40px 48px; color:#fff; }}

/* кнопки Streamlit: только синие. Состояния: hover, active, focus-visible, disabled */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
  border-radius:14px !important; min-height:46px; font-weight:700; font-size:15px; padding:.6rem 1.4rem; border:0; box-shadow:none;
  background:{BLUE_TINT}; color:{BLUE}; transition:background .15s, transform .08s, box-shadow .15s; }}
.stButton > button:hover {{ background:#DAE4FC; color:{BLUE_HOVER}; border:0; }}
.stButton > button:active {{ background:#C9D7FA; color:{BLUE_HOVER}; transform:scale(.98); }}
.stButton > button:focus-visible {{ outline:none; box-shadow:0 0 0 4px {RING}; }}
.stButton > button:disabled {{ background:#F0F2F8; color:#A9B6E2; cursor:not-allowed; transform:none; }}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {{ background:{BLUE}; color:#fff; }}
.stButton > button[kind="primary"]:hover {{ background:{BLUE_HOVER}; color:#fff; }}
.stButton > button[kind="primary"]:active {{ background:{BLUE_PRESSED}; color:#fff; }}
.stButton > button[kind="primary"]:disabled {{ background:#C5D0F3; color:#fff; }}
.stButton > button[kind="tertiary"] {{ background:transparent; color:{BLUE}; }}
.stButton > button[kind="tertiary"]:hover {{ background:#F0F3FE; }}
/* кнопки-варианты и «Выбрать»: главная на экране — основная (синяя заливка) */
[class*="st-key-main"] .stButton > button {{ background:{BLUE}; color:#fff; }}
[class*="st-key-main"] .stButton > button:hover {{ background:{BLUE_HOVER}; }}
[class*="st-key-main"] .stButton > button:active {{ background:{BLUE_PRESSED}; }}

/* поля ввода */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {{
  border-radius:14px !important; background:#fff !important; border:1.5px solid #D1D1D6 !important; min-height:46px;
  overflow:hidden; transition:border-color .15s, box-shadow .15s; }}
[data-baseweb="input"] input, [data-baseweb="base-input"] input, [data-baseweb="textarea"] textarea {{
  border:0 !important; border-radius:0 !important; background:transparent !important; box-shadow:none !important; }}
[data-baseweb="input"]:hover, [data-baseweb="textarea"]:hover, [data-baseweb="select"] > div:hover {{ border-color:#AEAEB2 !important; }}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {{
  border-color:{BLUE} !important; box-shadow:0 0 0 4px rgba(13,65,210,.16) !important; }}
/* шаг +/− у числовых полей: спокойные серые, на hover — светло-синяя подложка, не синяя заливка */
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] {{
  background:transparent !important; color:#6C6C70 !important; border-radius:12px !important; min-height:46px; }}
[data-testid="stNumberInputStepUp"]:hover, [data-testid="stNumberInputStepDown"]:hover {{ background:{BLUE_TINT} !important; color:{BLUE} !important; }}
[data-testid="stNumberInputStepUp"]:active, [data-testid="stNumberInputStepDown"]:active {{ background:#D3DFFB !important; }}
[data-baseweb="input"]:has(input:disabled), [data-baseweb="textarea"]:has(textarea:disabled) {{ background:#F2F2F7 !important; border-color:#E5E5EA !important; }}
[data-baseweb="input"]:has(input[aria-invalid="true"]), [data-baseweb="textarea"]:has(textarea[aria-invalid="true"]) {{
  border-color:#D93025 !important; box-shadow:0 0 0 4px rgba(217,48,37,.12) !important; }}
.stTextArea textarea {{ font-size:17px; }}
[data-testid="stNumberInput"] button:disabled {{ color:#C7C7CC !important; }}
label[data-baseweb="checkbox"] span, label[data-baseweb="radio"] span {{ transition:background .15s, border-color .15s; }}
[data-testid="stCheckbox"] label:hover span:first-child, [data-testid="stRadio"] label:hover > div:first-child {{ border-color:{BLUE}; }}

/* табы: линия, активный синий */
[data-baseweb="tab-list"] {{ gap:24px; border-bottom:1px solid #E5E5EA; }}
[data-baseweb="tab"] {{ font-weight:600; color:#6C6C70; padding:12px 4px; background:transparent; }}
[data-baseweb="tab"]:hover {{ color:#14161F; }}
[data-baseweb="tab"][aria-selected="true"] {{ color:{BLUE}; }}
[data-baseweb="tab"]:focus-visible {{ outline:none; box-shadow:0 0 0 4px {RING}; border-radius:8px; }}
[data-baseweb="tab"][disabled] {{ color:#C7C7CC; }}
[data-baseweb="tab-highlight"] {{ background:{BLUE}; height:2.5px; }}
[data-baseweb="tab-border"] {{ display:none; }}

/* поле запроса: одно и то же на всех экранах, на всю ширину контентной области; одна рамка, приподнято тенью */
[data-testid="stChatInput"] {{ width:100% !important; max-width:none !important; }}
[data-testid="stChatInput"] > div, [data-testid="stChatInput"] > div:hover {{
  background:#fff; border:2px solid {BLUE}; border-radius:20px; min-height:64px;
  box-shadow:0 12px 32px rgba(13,65,210,.16); transition:box-shadow .15s; }}
[data-testid="stChatInput"] > div:focus-within {{ border-color:{BLUE}; box-shadow:0 0 0 4px rgba(13,65,210,.16), 0 12px 32px rgba(13,65,210,.16); }}
[data-testid="stChatInput"] [data-baseweb="textarea"] {{ border:0 !important; box-shadow:none !important; min-height:0; border-radius:0 !important; overflow:visible; }}
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] {{ background:linear-gradient(180deg, rgba(245,245,247,0) 0%, #F5F5F7 45%) !important; }}
/* нижняя панель по ширине совпадает с контентной областью (.block-container) */
[data-testid="stBottomBlockContainer"] {{ max-width:1040px !important; width:100%; padding:8px 24px 24px !important; margin:0 auto; }}
[data-testid="stMain"], section.main {{ overflow-anchor:none; }}  /* браузер не «дотягивает» прокрутку к нижним карточкам */
/* поле ввода внизу: контейнер прижат к низу окна (см. ui/app.py) */
.st-key-dock {{ position:fixed; left:0; right:0; bottom:0; z-index:99; width:auto;
  padding:8px max(24px, calc((100vw - 1040px) / 2)) 24px; background:linear-gradient(180deg, rgba(245,245,247,0) 0%, #F5F5F7 45%); }}
.st-key-metrics-link {{ margin-top:4px; }}
.st-key-metrics-link [data-testid="stPageLink"] {{ justify-content:center; }}
.st-key-metrics-link [data-testid="stPageLink"] a {{ background:transparent; padding:4px 8px; }}
.st-key-metrics-link [data-testid="stPageLink"] p {{ font-size:13px; color:#86868B; }}
.st-key-metrics-link [data-testid="stPageLink"] a:hover p {{ color:{BLUE}; }}
.st-key-hero-search {{ width:100%; margin:28px 0 16px; padding:0; background:none; border:0; box-shadow:none; }}
[data-testid="stChatInput"] button {{ background:{BLUE}; color:#fff; border-radius:12px; }}
[data-testid="stChatInput"] button:disabled {{ background:#C5D0F3; }}
[data-testid="stExpander"], [data-testid="stExpander"] details {{ background:#fff !important; border:1px solid #E8E8ED !important; border-radius:24px !important;
  overflow:hidden; box-shadow:0 1px 2px rgba(20,30,60,.04), 0 8px 24px rgba(20,30,60,.04); }}
[data-testid="stExpander"] details {{ border:0 !important; box-shadow:none; }}
[data-testid="stExpander"] summary {{ padding:16px 24px; font-weight:600; background:transparent !important; }}
[data-testid="stExpanderDetails"] {{ padding:4px 24px 24px; }}
.od-glass.hover:hover, a.od-glass:hover {{ box-shadow:0 2px 4px rgba(20,30,60,.05),0 16px 36px rgba(20,30,60,.10); }}
.od-glass.sel {{ border:2px solid {BLUE}; }}
@media (max-width:760px) {{ .od-h1 {{ font-size:38px; }} .block-container {{ padding:12px 12px 96px; }} .od-steps .od-step span {{ display:none; }} }}
</style>
"""


def inject() -> None:
    import streamlit as st

    st.markdown(CSS, unsafe_allow_html=True)
