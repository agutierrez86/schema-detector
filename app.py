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

def fetch_html(url: str, timeout: int = 20) -> Tuple[Optional[str], Optional[int], Optional[str], Dict[str, str]]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    meta_tags = {}
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img: meta_tags["og_image"] = og_img.get("content", "")
        return r.text, r.status_code, None, meta_tags
    except Exception as e:
        return None, None, str(e), {}

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
        if match: return match.group(0).replace("T", " ")
        return date_str
    except: return str(date_str)

def analyze_multimedia(blocks: List[Any], meta_tags: Dict[str, str]) -> Dict[str, str]:
    res = {"primaryImageOfPage": "❌", "mainEntityImage": "❌", "ogImage": meta_tags.get("og_image", "❌"), "url_video": "❌"}
    seen_nodes = set()
    def get_url(val):
        if isinstance(val, dict): return val.get("url") or val.get("contentUrl")
        return val if isinstance(val, str) else None
    def walk(node: Any):
        if id(node) in seen_nodes: return
        seen_nodes.add(id(node))
        if isinstance(node, dict):
            if "primaryImageOfPage" in node:
                u = get_url(node["primaryImageOfPage"])
                if u: res["primaryImageOfPage"] = u
            t = str(node.get("@type", ""))
            if any(at in t for at in ["Article", "NewsArticle", "BlogPosting"]):
                if "image" in node:
                    u = get_url(node["image"])
                    if u: res["mainEntityImage"] = u
            if "VideoObject" in t:
                u = node.get("contentUrl") or node.get("embedUrl") or node.get("url")
                if u: res["url_video"] = u
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for it in node: walk(it)
    for b in blocks: walk(b)
    return res

def analyze_liveblog(blocks: List[Any]) -> Dict[str, Any]:
    update_dates = []
    created_date, last_modified = None, None
    fallback_created, fallback_modified = None, None
    seen_nodes = set()
    def walk(node: Any):
        nonlocal created_date, last_modified, fallback_created, fallback_modified
        if id(node) in seen_nodes: return
        seen_nodes.add(id(node))
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
            p_up = [datetime.fromisoformat(re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', d).group(0)) for d in update_dates if re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', d)]
            if len(p_up) > 1:
                p_up.sort()
                deltas = [(p_up[i] - p_up[i-1]).total_seconds() / 60 for i in range(1, len(p_up))]
                freq = round(sum(deltas) / len(deltas), 1)
        except: pass
    return {
        "creado": parse_date(created_date or fallback_created),
        "ultima_act": parse_date(last_modified or fallback_modified),
        "lb_freq": freq,
        "n_updates": len(update_dates)
    }

def extract_hierarchical_types(blocks: List[Any]) -> Tuple[List[str], List[str], Dict[str, Any], bool, str]:
    mains, subs = [], []
    dates = {"pub": None, "mod": None}
    has_auth, auth_name = False, "No identificado"
    seen_nodes = set()
    def get_auth(data):
        if isinstance(data, dict): return data.get("name") or data.get("alternateName")
        if isinstance(data, list) and len(data) > 0: return get_auth(data[0])
        return str(data) if data else None
    def walk(node: Any, is_root: bool):
        if id(node) in seen_nodes: return
        seen_nodes.add(id(node))
        nonlocal has_auth, auth_name
        if isinstance(node, dict):
            t = node.get("@type", "")
            curr = [t] if isinstance(t, str) else [str(x) for x in t]
            if is_root: mains.extend(curr)
            else: subs.extend(curr)
            if any(at in curr for at in ["Article", "NewsArticle", "BlogPosting", "LiveBlogPosting"]):
                if "author" in node and node["author"]:
                    has_auth, name = True, get_auth(node["author"])
                    if name: auth_name = name
            if node.get("datePublished") and not dates["pub"]: dates["pub"] = node.get("datePublished")
            if node.get("dateModified") and not dates["mod"]: dates["mod"] = node.get("dateModified")
            for k, v in node.items():
                if k == "@graph": walk(v, True)
                else: walk(v, False)
        elif isinstance(node, list):
            for it in node: walk(it, is_root)
    for b in blocks: walk(b, True)
    return list(dict.fromkeys(mains)), list(dict.fromkeys(subs)), dates, has_auth, auth_name

# --- INTERFAZ ---

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Columna URL", value="url")
    max_rows = st.number_input("Máx. filas", min_value=1, value=50)
    remove_dupes = st.checkbox("Quitar URLs duplicadas", value=True)

uploaded = st.file_uploader("Subí tu CSV", type=["csv"])

if uploaded is not None:
    try:
        df = pd.read_csv(uploaded)
        if remove_dupes and url_col in df.columns:
            df = df.drop_duplicates(subset=[url_col])
        if url_col not in df.columns:
            st.error(f"Hola! Por favor revisá que arriba a la izquierda... \n\nColumnas: {', '.join(list(df.columns))}")
            st.stop()

        if st.button("Procesar"):
            results = []
            progress = st.progress(0.0)
            df_subset = df.head(int(max_rows))
            for idx, url in enumerate(df_subset[url_col].tolist(), start=1):
                html, code, _, meta = fetch_html(str(url))
                row = {"url": url, "status": code}
                if html:
                    blocks, _ = parse_jsonld_from_html(html)
                    mains, subs, dates, has_auth, auth_name = extract_hierarchical_types(blocks)
                    lb_info = analyze_liveblog(blocks)
                    multi = analyze_multimedia(blocks, meta)
                    row.update({
                        "Type": ", ".join(mains), "Subtypes": ", ".join(subs),
                        "autor": auth_name, "firmado": has_auth,
                        **lb_info, **multi
                    })
                results.append(row)
                progress.progress(idx / len(df_subset))

            out = pd.DataFrame(results)

            # RESUMEN
            c1, c2, c3, c4 = st.columns(4)
            pct = lambda s: round((s.mean() * 100), 1) if not s.empty else 0
            c1.metric("% NewsArticle", f"{pct(out['Type'].str.contains('NewsArticle', na=False))}%")
            c2.metric("% Firmado", f"{pct(out['firmado'])}%")
            c3.metric("% Video", f"{pct(out['url_video'] != '❌')}%")
            c4.metric("% LiveBlog", f"{pct(out['Type'].str.contains('LiveBlogPosting', na=False))}%")

            t1, t2, t3 = st.tabs(["📋 General", "⏱️ Freshness & Live", "🎬 Multimedia"])
            with t1:
                st.dataframe(out[["url", "status", "Type", "Subtypes", "autor"]], use_container_width=True, hide_index=True)
            with t2:
                # RETORNO A LA LÓGICA ANTERIOR DE FRESHNESS
                st.subheader("Auditoría de Tiempos y Live Updates")
                # Se utiliza la estructura que tenías antes: Publicado, Actualizado y datos de LiveBlog
                st.dataframe(out[["url", "creado", "ultima_act", "lb_freq", "n_updates"]].rename(columns={
                    "creado": "creado", 
                    "ultima_act": "última actualización",
                    "lb_freq": "Frec. Prom (Min)", 
                    "n_updates": "número de actualizaciones"
                }), use_container_width=True, hide_index=True)
            with t3:
                st.dataframe(out[["url", "primaryImageOfPage", "mainEntityImage", "ogImage", "url_video"]], use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Error: {e}")

st.markdown("---")
st.markdown('<div style="text-align:center;">Sara vigila tu Schema - Creado por <strong>Agustín Gutierrez</strong></div>', unsafe_allow_html=True)
