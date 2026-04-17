"""Streamlit-Tool zur Visualisierung von Übergabestations-Daten

Start:
    uv run streamlit run app.py
"""
from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Konstanten / Defaults
# ---------------------------------------------------------------------------
# Config-Schema: erste Zeile = x-Achse, weitere Zeilen = y-Serien.
CONFIG_COLUMNS = [
    "Sichtbar",
    "Column",
    "Bezeichnung",
    "Einheit",
    "Skala min",
    "Skala max",
    "Farbe",
]
Y_ONLY_COLUMNS = ["Skala min", "Skala max", "Farbe", "Sichtbar"]


def _to_float_or_none(value) -> float | None:
    """Robuste float-Konvertierung: None/NaN/leer/ungültig -> None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _parse_bool(value, default: bool = True) -> bool:
    """Parst CSV-Bool ('true'/'false'/'1'/'0'/leer). Leer -> default."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("", "nan"):
        return default
    return s in ("true", "1", "yes", "ja", "x")
DEFAULT_PALETTE = pc.qualitative.Plotly + pc.qualitative.D3 + pc.qualitative.Set2

# Benannte Farbpalette für das Dropdown im Config-Editor.
# Der leere Eintrag "" steht für "automatisch".
NAMED_COLORS: dict[str, str] = {
    "": "",  # automatisch
    "Blau": "#1f77b4",
    "Orange": "#ff7f0e",
    "Grün": "#2ca02c",
    "Rot": "#d62728",
    "Violett": "#9467bd",
    "Braun": "#8c564b",
    "Pink": "#e377c2",
    "Grau": "#7f7f7f",
    "Oliv": "#bcbd22",
    "Türkis": "#17becf",
    "Hellblau": "#76b7ff",
    "Hellgrün": "#8ed18e",
    "Dunkelrot": "#8b0000",
    "Dunkelblau": "#00008b",
    "Schwarz": "#000000",
    "Gelb": "#ffd400",
    "Magenta": "#ff00ff",
    "Cyan": "#00ffff",
}
# Umkehr-Lookup: Hex-Wert (lowercase) -> Anzeigename
HEX_TO_NAME: dict[str, str] = {v.lower(): k for k, v in NAMED_COLORS.items() if v}


def color_to_name(value: str) -> str:
    """Wandelt einen gespeicherten Farbwert (Hex oder Name) in einen Dropdown-Namen."""
    v = (value or "").strip()
    if not v:
        return ""
    if v in NAMED_COLORS:  # bereits ein Name
        return v
    return HEX_TO_NAME.get(v.lower(), "")  # unbekanntes Hex -> leer = automatisch


def name_to_color(name: str) -> str:
    """Wandelt einen Dropdown-Namen in den Hex-Wert (leer = automatisch)."""
    return NAMED_COLORS.get((name or "").strip(), "")


# ---------------------------------------------------------------------------
# Hilfsfunktionen: Daten- und Config-Handling
# ---------------------------------------------------------------------------
def _read_csv_bytes(raw: bytes, sep: str = ";", decimal: str = ",") -> pd.DataFrame:
    """Lese CSV mit Auto-Encoding.

    Reihenfolge: utf-8-sig / utf-8 strikt zuerst (nur echte UTF-8-Dateien
    kommen durch), dann cp1252 als Fallback. cp1252 würde sonst jede Byte-
    Folge klaglos dekodieren und bei UTF-8-Dateien Mojibake produzieren.
    """
    # Strikter UTF-8-Check vorab auf den Rohbytes
    for enc in ("utf-8-sig", "utf-8"):
        try:
            raw.decode(enc)
        except UnicodeDecodeError:
            continue
        try:
            return pd.read_csv(
                io.BytesIO(raw), sep=sep, decimal=decimal, encoding=enc, engine="python"
            )
        except pd.errors.ParserError:
            pass
    # Fallback: Windows-1252 (typisch für deutsche Excel/SPS-Exports)
    for enc in ("cp1252", "latin-1"):
        try:
            return pd.read_csv(
                io.BytesIO(raw), sep=sep, decimal=decimal, encoding=enc, engine="python"
            )
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError("CSV konnte nicht eingelesen werden.")


def _try_parse_datetime(series: pd.Series) -> pd.Series:
    """Versuche eine Spalte als Datum zu parsen; bei Erfolg zurückgeben, sonst Original."""
    try:
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=False)
        if parsed.notna().sum() >= max(1, int(0.8 * len(series))):
            return parsed
    except Exception:
        pass
    return series


def load_data(uploaded) -> pd.DataFrame:
    df = _read_csv_bytes(uploaded.getvalue())
    df.columns = [c.strip() for c in df.columns]
    # Zeitpunkt-Spalte (falls vorhanden) parsen
    for col in df.columns:
        if col.lower() in ("zeitpunkt", "zeit", "datetime", "timestamp"):
            df[col] = _try_parse_datetime(df[col])
            break
    return df


def load_config(uploaded_or_path) -> pd.DataFrame:
    """Lädt eine Config. Erwartetes Schema siehe CONFIG_COLUMNS.

    Erste Zeile = x-Achse, alle weiteren Zeilen = y-Serien.
    """
    if hasattr(uploaded_or_path, "getvalue"):
        raw = uploaded_or_path.getvalue()
    else:
        raw = Path(uploaded_or_path).read_bytes()
    df = _read_csv_bytes(raw)
    df.columns = [c.strip() for c in df.columns]
    # 'Sichtbar' ist optional — fehlt sie, wird sie implizit auf True gesetzt.
    required = [c for c in CONFIG_COLUMNS if c != "Sichtbar"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Config-Datei hat nicht das erwartete Schema. Fehlende Spalten: "
            f"{', '.join(missing)}. Erwartet: {', '.join(required)}"
        )
    if "Sichtbar" not in df.columns:
        df["Sichtbar"] = True
    df["Sichtbar"] = df["Sichtbar"].map(lambda v: _parse_bool(v, default=True))
    return df[CONFIG_COLUMNS]


def config_to_csv_bytes(cfg: pd.DataFrame) -> bytes:
    # UTF-8 mit BOM: Excel erkennt Umlaute korrekt, keine Zeichenverluste.
    return cfg.to_csv(sep=";", index=False).encode("utf-8-sig")


def _clean_strings(cfg: pd.DataFrame) -> pd.DataFrame:
    """Trimmt String-Spalten und normalisiert NaN -> ''."""
    out = cfg.copy()
    for c in ("Column", "Bezeichnung", "Einheit", "Farbe"):
        if c in out.columns:
            out[c] = out[c].astype(str).where(out[c].notna(), "").str.strip()
            out.loc[out[c].str.lower() == "nan", c] = ""
    return out


def empty_config(columns: list[str]) -> pd.DataFrame:
    """Baue eine Default-Config (nur x-Zeile) aus vorhandenen Datenspalten."""
    x_candidate = next(
        (c for c in columns if c.lower() in ("zeitpunkt", "zeit", "datetime", "timestamp")),
        columns[0],
    )
    row = {
        "Column": x_candidate,
        "Bezeichnung": "Zeit",
        "Einheit": "",
        "Skala min": "",
        "Skala max": "",
        "Farbe": "",
        "Sichtbar": True,
    }
    return pd.DataFrame([row], columns=CONFIG_COLUMNS)


# ---------------------------------------------------------------------------
# Plot-Erzeugung
# ---------------------------------------------------------------------------
DISPLAY_MODES = ("Separate Y-Achsen je Serie", "Normiert (0…1)", "Gemeinsame Y-Achse")


def build_figure(
    df: pd.DataFrame,
    cfg: pd.DataFrame,
    mode: str,
    x_range=None,
) -> go.Figure:
    """Erzeugt das Diagramm. cfg.iloc[0] = x-Achse, cfg.iloc[1:] = y-Serien."""
    cfg = _clean_strings(cfg)
    cfg = cfg[cfg["Column"].str.len() > 0].reset_index(drop=True)
    if cfg.empty:
        raise ValueError("Config ist leer – keine x-Spalte definiert.")

    x_row = cfg.iloc[0]
    x_col = x_row["Column"]
    if x_col not in df.columns:
        raise ValueError(f"x-Spalte '{x_col}' nicht in Daten gefunden.")
    x_label = (x_row["Bezeichnung"] or x_col).strip() or x_col
    x_einheit = (x_row["Einheit"] or "").strip()
    x_title = f"{x_label} [{x_einheit}]" if x_einheit else x_label

    y_cfg = cfg.iloc[1:].reset_index(drop=True)
    if "Sichtbar" in y_cfg.columns:
        y_cfg = y_cfg[y_cfg["Sichtbar"].map(_parse_bool)].reset_index(drop=True)

    # Pro y-Serie effektive Werte bestimmen (inkl. Fallback auf Daten-Min/Max).
    series = []
    palette = DEFAULT_PALETTE
    for i, row in y_cfg.iterrows():
        col = row["Column"]
        if col not in df.columns:
            continue
        label = (row["Bezeichnung"] or col).strip() or col
        einheit = (row["Einheit"] or "").strip()
        color = (row.get("Farbe") or "").strip() or palette[i % len(palette)]
        vmin = _to_float_or_none(row["Skala min"])
        vmax = _to_float_or_none(row["Skala max"])
        y_raw = pd.to_numeric(df[col], errors="coerce")
        if (vmin is None or vmax is None) and y_raw.notna().any():
            dmin, dmax = float(y_raw.min()), float(y_raw.max())
            if vmin is None:
                vmin = round(dmin, 3)
            if vmax is None:
                vmax = round(dmax, 3) if dmax != dmin else round(dmin + 1, 3)
        series.append(dict(
            col=col, label=label, einheit=einheit, color=color,
            vmin=vmin, vmax=vmax, y_raw=y_raw,
        ))

    fig = go.Figure()

    if mode == "Separate Y-Achsen je Serie":
        _build_multi_axis(fig, series, df[x_col])
        xaxis_kwargs = dict(title=x_title, rangeslider=dict(visible=True))
    elif mode == "Normiert (0…1)":
        _build_normalized(fig, series, df[x_col])
        fig.update_layout(
            yaxis=dict(title="normierte Skala (0…1)", range=[-0.02, 1.02])
        )
        xaxis_kwargs = dict(title=x_title, rangeslider=dict(visible=True))
    else:  # Gemeinsame Y-Achse
        _build_raw(fig, series, df[x_col])
        fig.update_layout(yaxis=dict(title="Wert"))
        xaxis_kwargs = dict(title=x_title, rangeslider=dict(visible=True))

    fig.update_layout(
        template="plotly_dark",
        height=620,
        margin=dict(l=40, r=20, t=30, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=xaxis_kwargs,
    )
    if x_range is not None:
        fig.update_xaxes(range=list(x_range))
    return fig


def _build_normalized(fig: go.Figure, series: list[dict], x_data) -> None:
    for s in series:
        vmin, vmax = s["vmin"], s["vmax"]
        if vmin is None or vmax is None or vmax == vmin:
            continue
        y_plot = (s["y_raw"] - vmin) / (vmax - vmin)
        hu = f" {s['einheit']}" if s["einheit"] else ""
        fig.add_trace(go.Scatter(
            x=x_data, y=y_plot, mode="lines",
            name=f"{s['label']} [{vmin:g}…{vmax:g} {s['einheit']}]".strip(),
            line=dict(color=s["color"], width=1.5),
            customdata=s["y_raw"],
            hovertemplate=f"<b>{s['label']}</b>: %{{customdata:.2f}}{hu}<extra></extra>",
        ))


def _build_raw(fig: go.Figure, series: list[dict], x_data) -> None:
    for s in series:
        hu = f" {s['einheit']}" if s["einheit"] else ""
        fig.add_trace(go.Scatter(
            x=x_data, y=s["y_raw"], mode="lines",
            name=f"{s['label']}" + (f" [{s['einheit']}]" if s["einheit"] else ""),
            line=dict(color=s["color"], width=1.5),
            hovertemplate=f"<b>{s['label']}</b>: %{{y:.2f}}{hu}<extra></extra>",
        ))


def _build_multi_axis(fig: go.Figure, series: list[dict], x_data) -> None:
    """Je Serie eine eigene Y-Achse, links gestapelt in Seriefarbe.

    Die Einheit wird als zentrierte Annotation unterhalb der jeweiligen
    Achse gesetzt (statt als gedrehter axis title, der bei engem
    Stapeln überlappt).
    """
    n = len(series)
    if n == 0:
        return
    axis_step = 0.035
    # Primärachse (i=0) sitzt bei x=left_reserved, sekundäre bei
    # left_reserved - i*axis_step. Domain muss die äußerste Achse
    # einschließen; ein kleiner Puffer lässt Platz fürs Tick-Label.
    left_reserved = min(0.35, axis_step * max(n - 1, 0) + 0.015)
    x_domain = [left_reserved, 1.0]

    annotations = []

    for i, s in enumerate(series):
        vmin, vmax = s["vmin"], s["vmax"]
        hu = f" {s['einheit']}" if s["einheit"] else ""
        axis_id = "y" if i == 0 else f"y{i + 1}"
        fig.add_trace(go.Scatter(
            x=x_data, y=s["y_raw"], mode="lines",
            name=s["label"] + (f" [{s['einheit']}]" if s["einheit"] else ""),
            line=dict(color=s["color"], width=1.5),
            yaxis=axis_id,
            hovertemplate=f"<b>{s['label']}</b>: %{{y:.2f}}{hu}<extra></extra>",
        ))

        # Position der Achse: links, gestapelt von innen nach außen.
        # i=0 → left_reserved (Primärachse), i=k → left_reserved - k*axis_step.
        position = max(0.0, left_reserved - i * axis_step)
        axis_cfg = dict(
            title=None,  # Titel weglassen; stattdessen Annotation unten
            tickfont=dict(color=s["color"], size=10),
            linecolor=s["color"],
            ticks="outside",
            ticklen=4,
            tickcolor=s["color"],
            showgrid=(i == 0),  # nur erste Achse zeigt Gitter
            zeroline=False,
            range=[vmin, vmax] if vmin is not None and vmax is not None else None,
        )
        if i == 0:
            fig.update_layout(yaxis=axis_cfg)
        else:
            axis_cfg.update(
                overlaying="y",
                side="left",
                anchor="free",
                position=position,
            )
            fig.update_layout(**{f"yaxis{i + 1}": axis_cfg})

        if s["einheit"]:
            annotations.append(dict(
                x=position,
                y=-0.02,
                xref="paper", yref="paper",
                xanchor="center", yanchor="top",
                text=s["einheit"],
                showarrow=False,
                font=dict(color=s["color"], size=11),
            ))

    # xaxis-domain einschränken, Rangeslider deaktivieren (kollidiert sonst
    # mit den Einheits-Annotationen unter den Y-Achsen).
    fig.update_layout(
        xaxis=dict(domain=x_domain, rangeslider=dict(visible=False)),
        annotations=annotations,
    )


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="CSV Viewer", layout="wide")
st.title("CSV Viewer")

with st.sidebar:
    st.header("1) Daten")
    data_file = st.file_uploader("CSV-Datendatei", type=["csv", "CSV"])

    st.header("2) Diagramm-Konfiguration")
    project_dir = Path(__file__).parent
    default_cfg_dir = project_dir / "default_config"
    default_cfg_dir.mkdir(exist_ok=True)
    existing_cfgs = sorted(default_cfg_dir.glob("*.csv"))
    cfg_source = st.radio(
        "Config-Quelle",
        ["Vorhandene Config", "Config hochladen", "Neu aus Daten"],
        index=0 if existing_cfgs else 2,
    )

    cfg_df: pd.DataFrame | None = None
    cfg_name_hint = "config_diag_neu.csv"

    if cfg_source == "Vorhandene Config" and existing_cfgs:
        choice = st.selectbox("Config-Datei", existing_cfgs, format_func=lambda p: p.name)
        if choice is not None:
            cfg_df = load_config(choice)
            cfg_name_hint = choice.name
    elif cfg_source == "Config hochladen":
        up_cfg = st.file_uploader("Config-CSV", type=["csv", "CSV"], key="cfg_up")
        if up_cfg is not None:
            cfg_df = load_config(up_cfg)
            cfg_name_hint = up_cfg.name

    # Platzhalter für den Download-Button (wird weiter unten befüllt,
    # sobald die bearbeitete Config vorliegt).
    cfg_download_slot = st.container()

    st.header("3) Darstellung")
    display_mode = st.radio(
        "Y-Achsen-Modus",
        DISPLAY_MODES,
        index=0,
        help=(
            "Separate Achsen: wie Original-UI, jede Serie mit eigener Skala in "
            "ihrer Farbe. Normiert: alle Kurven auf 0…1 gemappt. Gemeinsam: "
            "alle auf einer Achse mit Rohwerten."
        ),
    )

if data_file is None:
    st.info("Bitte links eine CSV-Datendatei hochladen.")
    st.stop()

try:
    df = load_data(data_file)
except Exception as e:
    st.error(f"Fehler beim Einlesen der Daten: {e}")
    st.stop()

st.caption(f"Daten: {df.shape[0]:,} Zeilen × {df.shape[1]} Spalten")

# Config bereitstellen (falls nicht anderweitig geladen)
if cfg_df is None:
    cfg_df = empty_config(list(df.columns))

# Nur Spalten behalten, die auch in den Daten existieren (warnen sonst)
missing = [c for c in cfg_df["Column"] if c and c not in df.columns]
if missing:
    st.warning(f"Spalten in Config, aber nicht in Daten: {', '.join(missing)}")

# ---------------------------------------------------------------------------
# Editor: Config (X-Achse separat von Y-Serien)
# ---------------------------------------------------------------------------
all_cols = list(df.columns)

# --- X-Achse ---------------------------------------------------------------
st.subheader("X-Achse")
x_row = cfg_df.iloc[0]
x_default = x_row["Column"] if x_row["Column"] in all_cols else all_cols[0]
xc1, xc2, xc3 = st.columns([2, 2, 1])
with xc1:
    x_col_sel = st.selectbox(
        "Spalte", all_cols, index=all_cols.index(x_default), key="x_col"
    )
with xc2:
    x_label = st.text_input("Bezeichnung", value=str(x_row["Bezeichnung"]), key="x_label")
with xc3:
    x_einheit = st.text_input("Einheit", value=str(x_row["Einheit"]), key="x_einheit")

# --- Y-Serien --------------------------------------------------------------
st.subheader("Y-Serien")
y_df = cfg_df.iloc[1:].reset_index(drop=True)

# Farbwerte für Editor auf Dropdown-Namen mappen, Sichtbar als bool sicherstellen
y_for_editor = y_df.copy()
y_for_editor["Farbe"] = y_for_editor["Farbe"].map(color_to_name)
y_for_editor["Sichtbar"] = y_for_editor["Sichtbar"].map(
    lambda v: _parse_bool(v, default=True)
)

edited_y = st.data_editor(
    y_for_editor,
    num_rows="dynamic",
    width="stretch",
    column_config={
        "Column": st.column_config.SelectboxColumn(
            "Column", options=[c for c in all_cols if c != x_col_sel], required=True
        ),
        "Farbe": st.column_config.SelectboxColumn(
            "Farbe",
            options=list(NAMED_COLORS.keys()),
            help="Leer = automatische Farbzuweisung aus der Palette",
        ),
        "Skala min": st.column_config.NumberColumn("Skala min"),
        "Skala max": st.column_config.NumberColumn("Skala max"),
        "Sichtbar": st.column_config.CheckboxColumn(
            "Sichtbar",
            help="Nur aktivierte Serien werden im Diagramm dargestellt",
            default=True,
        ),
    },
    key="y_editor",
)
edited_y = edited_y.copy()
edited_y["Farbe"] = edited_y["Farbe"].map(name_to_color)
st.caption(
    "Neue Zeilen können direkt unten in der Tabelle hinzugefügt werden "
    "(leere letzte Zeile ausfüllen). Bleiben Skala min / max leer, werden "
    "automatisch die gerundeten Min-/Max-Werte aus den Daten verwendet."
)

# Gesamte Config zusammenbauen: x-Zeile + y-Zeilen
x_row_new = pd.DataFrame(
    [{
        "Column": x_col_sel,
        "Bezeichnung": x_label,
        "Einheit": x_einheit,
        "Skala min": "",
        "Skala max": "",
        "Farbe": "",
        "Sichtbar": True,
    }],
    columns=CONFIG_COLUMNS,
)
edited = pd.concat([x_row_new, edited_y], ignore_index=True)[CONFIG_COLUMNS]

# Download-Button in den reservierten Slot unter "2) Config" schreiben
with cfg_download_slot:
    st.download_button(
        "Aktuelle Config herunterladen",
        data=config_to_csv_bytes(edited),
        file_name=cfg_name_hint,
        mime="text/csv",
        width="stretch",
    )

# ---------------------------------------------------------------------------
# Zeitbereich vorbereiten
#
# Zwei State-Ebenen:
#   x_logical  — die *gewünschte* Auswahl (darf über den Datenbereich
#                hinausgehen, z.B. voller Tag 00:00–23:59 auch wenn Daten
#                erst um 06:00 beginnen).
#   time_slider — die *angezeigte* Auswahl, auf den Datenbereich geklemmt.
#
# Buttons schreiben x_logical (on_click, feuert VOR Rendering), der Plot
# und der Slider nutzen die geklemmte Fassung.  Wenn der Slider manuell
# bewegt wird, überschreibt on_change auch x_logical.
# ---------------------------------------------------------------------------
x_col = x_col_sel
x_series = df[x_col]
is_datetime = pd.api.types.is_datetime64_any_dtype(x_series)
is_numeric = pd.api.types.is_numeric_dtype(x_series)

if is_datetime:
    from datetime import datetime as _dt
    _xmin_raw = x_series.min().to_pydatetime()
    _xmax_raw = x_series.max().to_pydatetime()
    # Slider-Grenzen auf volle Stunden runden (abrunden / aufrunden),
    # damit die Schritte bei 0:00, 1:00, 2:00 … einrasten.
    xmin = _xmin_raw.replace(minute=0, second=0, microsecond=0)
    xmax = _xmax_raw.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
elif is_numeric:
    xmin = float(x_series.min())
    xmax = float(x_series.max())
else:
    xmin = xmax = None

_SLIDER_KEY = "time_slider"
_LOGICAL_KEY = "x_logical"

if xmin is not None:
    # Initialisieren (erster Lauf)
    if _LOGICAL_KEY not in st.session_state:
        st.session_state[_LOGICAL_KEY] = (xmin, xmax)
    if _SLIDER_KEY not in st.session_state:
        st.session_state[_SLIDER_KEY] = (xmin, xmax)

    # Geklemmte Fassung für Plot + Slider
    def _clamp(lo, hi):
        return (max(lo, xmin), min(hi, xmax))

    display_range = _clamp(*st.session_state[_LOGICAL_KEY])
    # Slider-State synchronisieren (nötig wenn Button den logischen Bereich
    # geändert hat, da der Slider seinen eigenen Key nutzt).
    st.session_state[_SLIDER_KEY] = display_range
    x_range = display_range
else:
    x_range = None

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
st.subheader("Diagramm")
try:
    fig = build_figure(df, edited, mode=display_mode, x_range=x_range)
    st.plotly_chart(fig, width="stretch", theme=None)
except Exception as e:
    st.error(f"Plot konnte nicht erstellt werden: {e}")

# ---------------------------------------------------------------------------
# Zeitbereich (unter dem Diagramm)
# ---------------------------------------------------------------------------
if xmin is not None:
    st.subheader("Zeitbereich")

    if is_datetime:
        def _set_logical(lo, hi):
            """Setzt den logischen Bereich (ungeklemmt)."""
            st.session_state[_LOGICAL_KEY] = (lo, hi)

        def _shift(delta):
            """Verschiebt den logischen Bereich um delta."""
            lo, hi = st.session_state[_LOGICAL_KEY]
            st.session_state[_LOGICAL_KEY] = (lo + delta, hi + delta)

        def _on_slider_change():
            """Slider manuell bewegt → logischen Bereich synchronisieren."""
            st.session_state[_LOGICAL_KEY] = st.session_state[_SLIDER_KEY]

        # Letzter Tag = Tag des letzten Datenpunkts (00:00 – 23:59),
        # auch wenn die Daten den Tag nicht vollständig abdecken.
        last_day_start = _dt(xmax.year, xmax.month, xmax.day, 0, 0, 0)
        last_day_end = last_day_start + timedelta(days=1)

        # Zeile 1: Schnellauswahl
        cols1 = st.columns(5)
        with cols1[0]:
            st.button("Letzte 1h", use_container_width=True,
                       on_click=_set_logical,
                       args=(xmax - timedelta(hours=1), xmax))
        with cols1[1]:
            st.button("Letzte 12h", use_container_width=True,
                       on_click=_set_logical,
                       args=(xmax - timedelta(hours=12), xmax))
        with cols1[2]:
            st.button("Letzte 24h", use_container_width=True,
                       on_click=_set_logical,
                       args=(xmax - timedelta(hours=24), xmax))
        with cols1[3]:
            st.button("Letzter Tag", use_container_width=True,
                       on_click=_set_logical,
                       args=(last_day_start, last_day_end))
        with cols1[4]:
            st.button("Alles", use_container_width=True,
                       on_click=_set_logical,
                       args=(xmin, xmax))

        # Zeile 2: Vor/Zurück
        cols2 = st.columns(4)
        with cols2[0]:
            st.button("− 1h", use_container_width=True,
                       on_click=_shift, args=(timedelta(hours=-1),))
        with cols2[1]:
            st.button("+ 1h", use_container_width=True,
                       on_click=_shift, args=(timedelta(hours=1),))
        with cols2[2]:
            st.button("− 24h", use_container_width=True,
                       on_click=_shift, args=(timedelta(hours=-24),))
        with cols2[3]:
            st.button("+ 24h", use_container_width=True,
                       on_click=_shift, args=(timedelta(hours=24),))

        st.slider(
            "Zeitfenster",
            min_value=xmin,
            max_value=xmax,
            step=timedelta(hours=1),
            format="YYYY-MM-DD HH:mm",
            key=_SLIDER_KEY,
            on_change=_on_slider_change,
        )
    elif is_numeric:
        st.slider("Bereich", min_value=xmin, max_value=xmax, key=_SLIDER_KEY)
else:
    st.caption(f"x-Spalte '{x_col}' ist kategorial – kein Slicing möglich.")
