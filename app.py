import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Detector de Datos Estructurados", layout="wide")

# --- FUNCIONES DE EXTRACCIÓN ---

def fetch_html(url: str, timeout: int = 20) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.text, r.status_code, None
    except Exception as e:
        return None, None, str(e)

def parse_jsonld_from_html(html: str) -> Tuple[List[Any], List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})
    blocks, errors = [], []
    for i, s in enumerate(scripts, start=1):
        raw = (s.string or s.get_text() or "").strip()
        if not raw: continue
        try:
            blocks.append(json.loads(raw))
        except Exception as e:
            errors.append(f"Bloque {i}: {e}")
    return blocks, errors

def parse_date(date_str: Any) -> Optional[datetime]:
    if not date_str or not isinstance(date_str, str): return None
    try:
        # Intenta parsear formatos ISO comunes (quitar zona horaria para simplificar)
        clean_date = date_str.split('+')[0].split('Z')[0]
        return datetime.fromisoformat(clean_date)
    except:
        return None

def analyze_liveblog(blocks: List[Any]) -> Dict[str, Any]:
    """Analiza la frecuencia y fechas de un LiveBlogPosting."""
    update_dates = []
    created_date = None
    last_modified = None
    
    def walk(node: Any):
        nonlocal created_date, last_modified
        if isinstance(node, dict):
            if node.get("@type") == "LiveBlogPosting":
                created_date = node.get("datePublished")
                last_modified = node.get("dateModified")
                
                # Buscar las actualizaciones (pueden estar en liveBlogUpdate)
                updates = node.get("liveBlogUpdate", [])
                if isinstance(updates, dict): updates = [updates]
                
                for up in updates:
                    d = up.get("datePublished") or up.get("dateModified")
                    parsed = parse_date(d)
                    if parsed: update_dates.append(parsed)
            
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for it in node: walk(it)

    for b in blocks: walk(b)
    
    # Calcular frecuencia
    freq = 0
    if len(update_dates) > 1:
        update_dates.sort()
        deltas = [(update_dates[i] - update_dates[i-1]).total_seconds() / 60 
                  for i in range(1, len(update_dates))]
        freq = round(sum(deltas) / len(deltas), 1)
        
    return {
        "lb_created": created_date,
        "lb_modified": last_modified,
        "lb_avg_freq": freq,
        "lb_updates_count": len(update_dates)
    }

def extract_hierarchical_types(blocks: List[Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    mains, subtypes = [], []
    dates = {"pub": None, "mod": None}

    def walk(node: Any, is_root: bool):
        if isinstance(node, dict):
            if "@type" in node:
                t = node["@type"]
                current_types = [t] if isinstance(t, str) else [str(x) for x in t]
                if is_root: mains.extend(current_types)
                else: subtypes.extend(current_types)
                
                # Extraer fechas si es NewsArticle
                if "NewsArticle" in current_types:
                    dates["pub"] = node.get("datePublished")
                    dates["mod"] = node.get("dateModified")
            
            for k, v in node.items():
                if k == "@graph": walk(v, True)
                else: walk(v, False)
        elif isinstance(node, list):
            for it in node: walk(it, is_root)

    for b in blocks: walk(b, True)
    return list(dict.fromkeys(mains)), list(dict.fromkeys(subtypes)), dates

# --- INTERFAZ ---

st.title("Detector de Datos Estructurados Pro")

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Columna URL", value="url")
    max_rows = st.number_input("Máx. filas", min_value=1, value=50)

uploaded = st.file_uploader("Subí tu CSV", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded).head(int(max_rows))

    if st.button("Procesar"):
        results = []
        for idx, url in enumerate(df[url_col].tolist(), start=1):
            html, code, _ = fetch_html(url)
            row = {"url": url, "status": code, "Type": "", "Subtype": "", "has_author": False,
                   "news_pub": None, "news_mod": None, "lb_freq": 0, "lb_created": None, "lb_mod": None}

            if html:
                blocks, _ = parse_jsonld_from_html(html)
                mains, subs, dates = extract_hierarchical_types(blocks)
                lb_info = analyze_liveblog(blocks)
                
                row.update({
                    "Type": ", ".join(mains),
                    "Subtype": ", ".join(subs),
                    "has_author": any("author" in str(b) for b in blocks),
                    "news_pub": dates["pub"],
                    "news_mod": dates["mod"],
                    "lb_freq": lb_info["lb_avg_freq"],
                    "lb_created": lb_info["lb_created"],
                    "lb_mod": lb_info["lb_modified"]
                })
            results.append(row)

        out = pd.DataFrame(results)

        # =========================
        # PESTAÑAS DE RESULTADOS
        # =========================
        tab_resumen, tab_temporal = st.tabs(["📊 Resumen y Tabla", "⏱️ Análisis Temporal"])

        with tab_resumen:
            def has_t(t): return out["Type"].str.contains(rf"(^|,\s*){re.escape(t)}(,\s*|$)", regex=True)
            pct = lambda s: round((s.mean() * 100), 1)
            
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("% NewsArticle", f"{pct(has_t('NewsArticle'))}%")
            c2.metric("% Article", f"{pct(has_t('Article'))}%")
            c3.metric("% Author", f"{pct(out['has_author'])}%")
            c4.metric("% VideoObject", f"{pct(has_t('VideoObject'))}%")
            c5.metric("% LiveBlog", f"{pct(has_t('LiveBlogPosting'))}%")
            
            st.dataframe(out[["url", "status", "Type", "Subtype"]], use_container_width=True)

        with tab_temporal:
            st.subheader("Análisis de Frescura (Freshness)")
            
            col_news, col_lb = st.columns(2)
            
            with col_news:
                st.markdown("#### 📰 NewsArticle Dates")
                st.dataframe(out[out["Type"].str.contains("NewsArticle", na=False)][["url", "news_pub", "news_mod"]])
            
            with col_lb:
                st.markdown("#### 🔴 LiveBlog Update Frequency")
                lb_only = out[out["Type"].str.contains("LiveBlogPosting", na=False)]
                st.dataframe(lb_only[["url", "lb_freq", "lb_created", "lb_mod"]].rename(columns={"lb_freq": "Freq (Min)"}))

# --- FIRMA ---
st.markdown("---")
logo_url = "https://cdn-icons-png.flaticon.com/512/174/174857.png" 
st.markdown(f"""
    <div style="display: flex; align-items: center; justify-content: center; gap: 15px;">
        <img src="{logo_url}" width="30">
        <div style="font-size: 14px;">
            Creado por <strong>Agustín Gutierrez</strong><br>
            <a href="https://www.linkedin.com/in/agutierrez86/" target="_blank">LinkedIn</a>
        </div>
    </div>
    """, unsafe_allow_html=True)
