import json
import re
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Detector de Datos Estructurados", layout="wide")


def fetch_html(url: str, timeout: int = 20) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.text, r.status_code, None
    except Exception as e:
        return None, None, str(e)


def parse_jsonld_from_html(html: str) -> Tuple[List[Any], List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})

    blocks: List[Any] = []
    errors: List[str] = []

    for i, s in enumerate(scripts, start=1):
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except Exception as e:
            errors.append(f"Bloque {i}: {e}")

    return blocks, errors


def extract_types(block: Any) -> List[str]:
    """
    Extrae @type de distintos formatos:
    - dict con @type
    - lista de items
    - @graph
    """
    types: List[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            if "@type" in node:
                t = node["@type"]
                if isinstance(t, list):
                    types.extend([str(x) for x in t])
                else:
                    types.append(str(t))
            if "@graph" in node:
                walk(node["@graph"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(block)

    # dedupe manteniendo orden
    seen = set()
    out: List[str] = []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def has_author_field(block: Any) -> bool:
    """
    Devuelve True si encuentra el campo 'author' en cualquier nivel del JSON-LD.
    """
    found = False

    def walk(node: Any):
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            if "author" in node:
                found = True
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(block)
    return found


st.title("Detector de datos estructurados desde CSV")
st.caption("Subí un CSV con URLs y obtené qué schemas/@type aparecen por página (JSON-LD).")

with st.sidebar:
    st.header("Opciones")
    url_col = st.text_input("Nombre de la columna de URL", value="url")
    timeout = st.slider("Timeout por URL (segundos)", 5, 60, 20)
    max_rows = st.number_input("Máx. filas a procesar (para pruebas)", min_value=1, value=50, step=1)
    show_raw = st.checkbox("Incluir JSON-LD crudo (pesado)", value=False)

uploaded = st.file_uploader("Subí tu CSV", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded)

    if url_col not in df.columns:
        st.error(f"No encuentro la columna '{url_col}'. Columnas disponibles: {list(df.columns)}")
        st.stop()

    df = df.copy()
    df[url_col] = df[url_col].astype(str).str.strip()
    df = df[df[url_col].str.len() > 0].head(int(max_rows))

    st.write(f"Filas a procesar: **{len(df)}**")

    if st.button("Procesar"):
        results: List[Dict[str, Any]] = []
        progress = st.progress(0.0)
        status = st.empty()

        urls = df[url_col].tolist()

        for idx, url in enumerate(urls, start=1):
            status.write(f"Procesando {idx}/{len(urls)}: {url}")
            html, code, err = fetch_html(url, timeout=int(timeout))

            row: Dict[str, Any] = {
                "url": url,
                "http_status": code,
                "fetch_error": err,
                "jsonld_blocks": 0,
                "types": "",
                "types_count": 0,
                "parse_errors": "",
                "has_author": False,  # ✅ campo real (no @type)
            }

            if html:
                blocks, parse_errors = parse_jsonld_from_html(html)
                row["jsonld_blocks"] = len(blocks)
                row["parse_errors"] = " | ".join(parse_errors) if parse_errors else ""

                # ✅ author real
                for b in blocks:
                    if has_author_field(b):
                        row["has_author"] = True
                        break

                # tipos
                all_types: List[str] = []
                for b in blocks:
                    all_types.extend(extract_types(b))

                seen = set()
                dedup: List[str] = []
                for t in all_types:
                    if t not in seen:
                        seen.add(t)
                        dedup.append(t)

                row["types"] = ", ".join(dedup)
                row["types_count"] = len(dedup)

                if show_raw:
                    row["jsonld_raw"] = json.dumps(blocks, ensure_ascii=False)

            results.append(row)
            progress.progress(idx / len(urls))

        out = pd.DataFrame(results)

        # =========================
        # Resumen automático (%)
        # =========================
        st.subheader("Resumen automático")

        out["types"] = out["types"].fillna("")

        def has_type(t: str) -> pd.Series:
            # match exacto por token (evita falsos positivos)
            pattern = rf"(^|,\s*){re.escape(t)}(,\s*|$)"
            return out["types"].str.contains(pattern, regex=True)

        pct = lambda s: round((s.mean() * 100), 1)

        has_newsarticle = has_type("NewsArticle")
        has_article = has_type("Article")
        has_videoobject = has_type("VideoObject")
        has_liveblog = has_type("LiveBlogPosting")
        has_author = out["has_author"].fillna(False)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("% con NewsArticle", f"{pct(has_newsarticle)}%")
        c2.metric("% con Article", f"{pct(has_article)}%")
        c3.metric("% con author", f"{pct(has_author)}%")
        c4.metric("% con VideoObject", f"{pct(has_videoobject)}%")
        c5.metric("% con LiveBlog", f"{pct(has_liveblog)}%")


        # =========================
        # Resultados + descarga
        # =========================
        st.subheader("Resultados")
        st.dataframe(out, use_container_width=True)

        st.subheader("Descargar CSV")
        csv_bytes = out.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Descargar resultados",
            data=csv_bytes,
            file_name="resultado_schema.csv",
            mime="text/csv",
        )

else:
    st.info("Subí un CSV para empezar.")


# =========================
# Firma
# =========================
st.markdown("---")
st.markdown(
    """
    <div style="text-align:center; font-size:14px;">
        Creado por <strong>Agustín Gutierrez</strong><br>
        <a href="https://www.linkedin.com/in/agutierrez86/" target="_blank">
            Ver perfil en LinkedIn
        </a>
    </div>
    """,
    unsafe_allow_html=True
)



