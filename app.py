import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Sara vigila tu Schema", layout="wide")

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

def parse_date(date_str: Any) -> Optional[str]:
    if not date_str or not isinstance(date_str, str): return None
    try:
        match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', date_str)
        if match:
            return match.group(0).replace("T", " ")
        return date_str
    except:
        return str(date_str)

def analyze_liveblog(blocks: List[Any]) -> Dict[str, Any]:
    update_dates = []
    created_date, last_modified = None, None
    fallback_created, fallback_modified = None, None

    def walk(node: Any):
        nonlocal created_date, last_modified, fallback_created, fallback_modified
        if isinstance(node, dict):
            if node.get("datePublished"): fallback_created = node.get("datePublished")
            if node.get("dateModified"): fallback_modified = node.get("dateModified")

            if "LiveBlogPosting" in str(node.get("@type", "")):
                created_date = node.get("datePublished")
                last_modified = node.get("dateModified")
                updates = node.get("liveBlogUpdate", [])
                if isinstance(updates, dict): updates = [updates]
                for up in updates:
                    d = up.get("datePublished") or up.get("dateModified")
                    if d: update_dates.append(d)
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for it in node: walk(it)

    for b in blocks: walk(b)
    
    freq = 0
    if len(update_dates) > 1:
        try:
            parsed_updates = []
            for d in update_dates:
                match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', d)
                if match: parsed_updates.append(datetime.fromisoformat(match.group(0)))
            if len(parsed_updates) > 1:
                parsed_updates.sort()
                deltas = [(parsed_updates[i] - parsed_updates[i-1]).total_seconds() / 60 for i in range(1, len(parsed_updates))]
                freq = round(sum(deltas) / len(deltas), 1)
        except: pass
        
    return {
        "creado": parse_date(created_date or fallback_created),
        "ultima_act": parse_date(last_modified or fallback_modified),
        "lb_avg_freq": freq,
        "n_updates": len(update_dates)
    }

def extract_hierarchical_types(blocks: List[Any]) -> Tuple[List[str], List[str], Dict[str, Any], bool, str]:
    mains, subtypes = [], []
    dates = {"pub": None, "mod": None}
    has_real_author = False
    author_name = "No identificado"

    def get_author_name(author_data):
        if isinstance(author_data, dict):
            return author_data.get("name") or author_data.get("alternateName")
        if isinstance(author_data, list) and len(author_data) > 0:
            return get_author_name(author_data[0])
        return str(author_data) if author_data else None

    def walk(node: Any, is_root: bool):
        nonlocal has_real_author, author_name
        if isinstance(node, dict):
            t = node.get("@type", "")
            current_types = [t] if isinstance(t, str) else [str(x) for x in t]
            
            if is_root: mains.extend(current_types)
            else: subtypes.extend(current_types)

            # --- LÓGICA DE AUTOR REFORZADA ---
            article_types = ["Article", "NewsArticle", "BlogPosting", "LiveBlogPosting"]
            if any(at in current_types for at in article_types):
                if "author" in node and node["author"]:
                    has_real_author = True
                    name = get_author_name(node["author"])
                    if name: author_name = name

            if node.get("datePublished") and not dates["pub"]:
                dates["pub"] = node.get("datePublished")
            if node.get("dateModified") and not dates["mod"]:
                dates["mod"] = node.get("dateModified")
            
            for k, v in node.items():
                if k == "@graph": walk(v, True)
                else: walk(v, False)
        elif isinstance(node, list):
            for it in node: walk(it, is_root)

    for b in blocks: walk(b, True)
    return list(dict.fromkeys(mains)), list(dict.fromkeys(subtypes)), dates, has_real_author, author_name

# --- INTERFAZ ---

st.title("Sara vigila tu Schema")

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Columna URL", value="url")
    max_rows = st.number_input("Máx. filas", min_value=1, value=50)

uploaded = st.file_uploader("Subí tu CSV", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded)
    
    if url_col not in df.columns:
        st.error(f"""
        Hola! Por favor revisá que arriba a la izquierda el nombre de Columna URL coincida con el nombre de la columna donde están las urls de tu csv. Gracias! Abrazo virtual!
        ---
        Hi! Please check that the 'Columna URL' name on the top left matches the name of the column where the URLs are in your CSV. Thanks! Virtual hug!
        ---
        🧧 如果你为了寻找错误而特意翻译这段文字，我祝贺你：时刻核实你在网上看到的一切是个好习惯。拥抱！！
        ---
        **Columnas detectadas / Detected columns:** {list(df.columns)}
        """)
        st.stop()

    df_subset = df.head(int(max_rows))

    if st.button("Procesar"):
        results = []
        progress = st.progress(0.0)
        
        for idx, url in enumerate(df_subset[url_col].tolist(), start=1):
            html, code, _ = fetch_html(str(url))
            row = {"url": url, "status": code, "Type": "", "Subtype": "", "has_author": False, "author_name": "",
                   "creado": None, "ultima_act": None, "lb_freq": 0, "lb_creado": None, "lb_ultima_act": None, "lb_updates": 0}

            if html:
                blocks, _ = parse_jsonld_from_html(html)
                mains, subs, dates, has_auth, auth_name = extract_hierarchical_types(blocks)
                lb_info = analyze_liveblog(blocks)
                
                row.update({
                    "Type": ", ".join(mains),
                    "Subtype": ", ".join(subs),
                    "has_author": has_auth,
                    "author_name": auth_name,
                    "creado": parse_date(dates["pub"]),
                    "ultima_act": parse_date(dates["mod"]),
                    "lb_freq": lb_info["lb_avg_freq"],
                    "lb_creado": lb_info["creado"],
                    "lb_ultima_act": lb_info["ultima_act"],
                    "lb_updates": lb_info["n_updates"]
                })
            results.append(row)
            progress.progress(idx / len(df_subset))

        out = pd.DataFrame(results)

        # RESUMEN MÉTRICAS
        st.subheader("Resumen automático")
        def has_t(t): return out["Type"].str.contains(rf"(^|,\s*){re.escape(t)}(,\s*|$)", regex=True)
        pct = lambda s: round((s.mean() * 100), 1)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("% NewsArticle", f"{pct(has_t('NewsArticle'))}%")
        c2.metric("% Article", f"{pct(has_t('Article'))}%")
        c3.metric("% Firmado (Author)", f"{pct(out['has_author'])}%")
        c4.metric("% VideoObject", f"{pct(has_t('VideoObject'))}%")
        c5.metric("% LiveBlog", f"{pct(has_t('LiveBlogPosting'))}%")
        
        st.divider()

        tab_general, tab_freshness = st.tabs(["📋 Resultados Generales", "⏱️ Freshness & Live Update"])

        with tab_general:
            st.subheader("Análisis de Autoría y Tipos")
            st.dataframe(out[["url", "status", "Type", "author_name", "has_author"]].rename(columns={"author_name": "autor", "has_author": "firmado"}), use_container_width=True, hide_index=True)
            csv_bytes = out.to_csv(index=False).encode("utf-8")
            st.download_button("Descargar CSV", data=csv_bytes, file_name="analisis_seo.csv")

        with tab_freshness:
            col_news, col_lb = st.columns(2)
            with col_news:
                st.markdown("**📰 Fechas NewsArticle / Article**")
                n_df = out[out["Type"].str.contains("NewsArticle|Article", na=False)][["url", "creado", "ultima_act"]]
                st.dataframe(n_df.rename(columns={"creado": "creado", "ultima_act": "última actualización"}), use_container_width=True, hide_index=True)
            with col_lb:
                st.markdown("**🔴 LiveBlog: Frecuencia y Fechas**")
                l_df = out[out["Type"].str.contains("LiveBlogPosting", na=False)][["url", "lb_freq", "lb_updates", "lb_creado", "lb_ultima_act"]]
                st.dataframe(l_df.rename(columns={"lb_freq": "Frec. Prom (Min)", "lb_updates": "número de actualizaciones", "lb_creado": "creado", "lb_ultima_act": "última actualización"}), use_container_width=True, hide_index=True)

# Firma
st.markdown("---")
logo_url = "https://cdn-icons-png.flaticon.com/512/174/174857.png" 
st.markdown(f'<div style="display:flex;align-items:center;justify-content:center;gap:15px;"><img src="{logo_url}" width="30"><div>Creado por <strong>Agustín Gutierrez</strong><br><a href="https://www.linkedin.com/in/agutierrez86/" target="_blank">LinkedIn</a></div></div>', unsafe_allow_html=True)
