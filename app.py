import json
import re
from typing import Any, Dict, List, Tuple, Optional
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# Configuración de página
st.set_page_config(page_title="Detector de Datos Estructurados", layout="wide")

# --- FUNCIONES DE EXTRACCIÓN Y LÓGICA ---

def fetch_html(url: str, timeout: int = 20) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
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

def extract_types(block: Any) -> List[str]:
    types = []
    def walk(node: Any):
        if isinstance(node, dict):
            if "@type" in node:
                t = node["@type"]
                if isinstance(t, list): types.extend([str(x) for x in t])
                else: types.append(str(t))
            if "@graph" in node: walk(node["@graph"])
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for it in node: walk(it)
    walk(block)
    return list(dict.fromkeys(types))

def check_nested(blocks: List[Any], parent_type: str, child_key_or_type: str) -> bool:
    """Verifica si un ParentType tiene un ChildType o propiedad anidada."""
    def search(node: Any, p_found: bool) -> bool:
        if isinstance(node, dict):
            current_types = node.get("@type", [])
            if isinstance(current_types, str): current_types = [current_types]
            
            # Verificamos si este nodo es el padre buscado
            is_parent = parent_type in current_types
            
            if p_found or is_parent:
                # Buscamos el hijo en este nivel (como propiedad o como @type)
                if child_key_or_type in node or child_key_or_type in str(node.get("@type", "")):
                    return True
                # Seguir buscando dentro de las ramas de este padre
                return any(search(v, True) for v in node.values())
            
            # Si no es el padre, seguimos buscando el padre en niveles inferiores
            return any(search(v, False) for v in node.values())
        elif isinstance(node, list):
            return any(search(it, p_found) for it in node)
        return False
    
    return any(search(b, False) for b in blocks)

# --- INTERFAZ STREAMLIT ---

st.title("Detector de datos estructurados (Jerárquico)")
st.caption("Subí un CSV para analizar Schemas Principales y sus elementos anidados.")

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Nombre de la columna de URL", value="url")
    timeout = st.slider("Timeout por URL (segundos)", 5, 60, 20)
    max_rows = st.number_input("Máx. filas a procesar", min_value=1, value=50, step=1)
    show_raw = st.checkbox("Incluir JSON-LD crudo (pesado)", value=False)

uploaded = st.file_uploader("Subí tu CSV", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded)
    if url_col not in df.columns:
        st.error(f"No encuentro la columna '{url_col}'.")
        st.stop()

    df = df.copy().head(int(max_rows))
    st.write(f"Filas a procesar: **{len(df)}**")

    if st.button("Procesar"):
        results = []
        progress = st.progress(0.0)
        status = st.empty()

        for idx, url in enumerate(df[url_col].tolist(), start=1):
            status.write(f"Procesando {idx}/{len(df)}: {url}")
            html, code, err = fetch_html(url, timeout=int(timeout))

            row = {
                "url": url,
                "http_status": code,
                "news_main": False,
                "news_with_image": False,
                "news_with_author": False,
                "article_main": False,
                "liveblog_main": False,
                "video_main": False,
                "types": ""
            }

            if html:
                blocks, _ = parse_jsonld_from_html(html)
                all_types = []
                for b in blocks:
                    all_types.extend(extract_types(b))
                
                row["types"] = ", ".join(list(dict.fromkeys(all_types)))
                
                # Identificación de principales
                row["news_main"] = "NewsArticle" in all_types
                row["article_main"] = "Article" in all_types
                row["liveblog_main"] = "LiveBlogPosting" in all_types
                row["video_main"] = "VideoObject" in all_types
                
                # Identificación de anidados (Jerarquía)
                if row["news_main"]:
                    row["news_with_image"] = check_nested(blocks, "NewsArticle", "ImageObject")
                    row["news_with_author"] = check_nested(blocks, "NewsArticle", "author")
                
                if show_raw:
                    row["jsonld_raw"] = json.dumps(blocks, ensure_ascii=False)

            results.append(row)
            progress.progress(idx / len(df))

        out = pd.DataFrame(results)
        pct = lambda s: round((s.mean() * 100), 1)

        # --- SECCIÓN DE RESUMEN ---
        st.subheader("📊 Resumen de Datos Estructurados")
        
        col_root, col_nest = st.columns(2)
        
        with col_root:
            st.info("### Schemas Principales")
            c1, c2 = st.columns(2)
            c1.metric("NewsArticle", f"{pct(out['news_main'])}%")
            c1.metric("LiveBlogPosting", f"{pct(out['liveblog_main'])}%")
            c2.metric("Article", f"{pct(out['article_main'])}%")
            c2.metric("VideoObject", f"{pct(out['video_main'])}%")

        with col_nest:
            st.success("### Detalle Anidado")
            a1, a2 = st.columns(2)
            a1.metric("NewsArticle > Image", f"{pct(out['news_with_image'])}%")
            a2.metric("NewsArticle > Author", f"{pct(out['news_with_author'])}%")
            st.caption("Muestra si la entidad hija está DENTRO del NewsArticle.")

        st.divider()
        st.subheader("Resultados")
        st.dataframe(out, use_container_width=True)

        csv_bytes = out.to_csv(index=False).encode("utf-8")
        st.download_button("Descargar CSV", data=csv_bytes, file_name="resultado_schema_seo.csv")

# --- FIRMA ---
st.markdown("---")
st.markdown(
    """<div style="text-align:center; font-size:14px;">
    Creado por <strong>Agustín Gutierrez</strong><br>
    <a href="https://www.linkedin.com/in/agutierrez86/" target="_blank">LinkedIn</a>
    </div>""", unsafe_allow_html=True
)
