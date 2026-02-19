import json
import re
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

def extract_hierarchical_types(blocks: List[Any]) -> Tuple[List[str], List[str]]:
    """Separa tipos principales (raíz) de tipos anidados."""
    mains = []
    subtypes = []

    def walk(node: Any, is_root: bool):
        if isinstance(node, dict):
            if "@type" in node:
                t = node["@type"]
                current_types = [t] if isinstance(t, str) else [str(x) for x in t]
                if is_root:
                    mains.extend(current_types)
                else:
                    subtypes.extend(current_types)
            
            # Al entrar en cualquier propiedad, el nivel siguiente es anidado
            for k, v in node.items():
                if k == "@graph": # El @graph contiene nodos raíz
                    walk(v, True)
                else:
                    walk(v, False)
        elif isinstance(node, list):
            for it in node:
                walk(it, is_root)

    for b in blocks:
        walk(b, True)
    
    return list(dict.fromkeys(mains)), list(dict.fromkeys(subtypes))

# --- INTERFAZ ---

st.title("Detector de datos estructurados desde CSV")
st.caption("Analiza URLs para diferenciar Schemas principales de sus elementos anidados.")

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Nombre de la columna de URL", value="url")
    timeout = st.slider("Timeout por URL (segundos)", 5, 60, 20)
    max_rows = st.number_input("Máx. filas a procesar", min_value=1, value=50, step=1)

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
                "Type": "",
                "Subtype": "",
                "has_author": False
            }

            if html:
                blocks, _ = parse_jsonld_from_html(html)
                mains, subs = extract_hierarchical_types(blocks)
                
                # Check de author para el resumen
                author_found = any("author" in str(b) for b in blocks)
                
                row["Type"] = ", ".join(mains)
                row["Subtype"] = ", ".join(subs)
                row["has_author"] = author_found

            results.append(row)
            progress.progress(idx / len(df))

        out = pd.DataFrame(results)

        # =========================
        # Resumen automático (Como el original)
        # =========================
        st.subheader("Resumen automático")
        
        def has_type_in_main(t: str) -> pd.Series:
            return out["Type"].str.contains(rf"(^|,\s*){re.escape(t)}(,\s*|$)", regex=True)

        pct = lambda s: round((s.mean() * 100), 1)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("% con NewsArticle", f"{pct(has_type_in_main('NewsArticle'))}%")
        c2.metric("% con Article", f"{pct(has_type_in_main('Article'))}%")
        c3.metric("% con author", f"{pct(out['has_author'])}%")
        c4.metric("% con VideoObject", f"{pct(has_type_in_main('VideoObject'))}%")
        c5.metric("% con LiveBlog", f"{pct(has_type_in_main('LiveBlogPosting'))}%")

        # =========================
        # Resultados finales
        # =========================
        st.subheader("Resultados")
        # Columnas: url, http_status, Type, Subtype
        final_cols = ["url", "http_status", "Type", "Subtype"]
        st.dataframe(out[final_cols], use_container_width=True)

        csv_bytes = out[final_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Descargar resultados",
            data=csv_bytes,
            file_name="resultado_schema_completo.csv",
            mime="text/csv",
        )

# Firma
st.markdown("---")
st.markdown(
    """<div style="text-align:center; font-size:14px;">
    Creado por <strong>Agustín Gutierrez</strong><br>
    <a href="https://www.linkedin.com/in/agutierrez86/" target="_blank">LinkedIn</a>
    </div>""", unsafe_allow_html=True
)
