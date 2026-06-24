from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from drsm_core import (
    ANALYSIS_DIR,
    APP_TITLE,
    APP_VERSION,
    DEFAULT_MODEL,
    EXPORTS_DIR,
    UPLOADS_DIR,
    WORK_DIR,
    audio_duration,
    dependency_status,
    export_clips,
    export_title_for,
    format_time,
    import_av,
    import_whisper_model_class,
    load_analysis,
    parse_time,
    safe_filename,
    save_analysis,
    segment_course,
    transcribe_audio,
)


st.set_page_config(page_title=APP_TITLE, layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }
    div[data-testid="stDataFrame"],
    div[data-testid="stDataEditor"] {
        width: 100%;
    }
    .drsm-panel {
        border: 1px solid rgba(49, 51, 63, 0.18);
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 1rem;
        background: rgba(250, 250, 250, 0.55);
    }
    .drsm-muted {
        color: rgba(49, 51, 63, 0.72);
        font-size: 0.92rem;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        min-height: 2.6rem;
        padding: 0.35rem 0.2rem;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        font-size: 1.08rem;
        font-weight: 650;
    }
    section[data-testid="stSidebar"] h2 {
        font-size: 1.18rem;
    }
    section[data-testid="stSidebar"] .stButton button {
        width: 100%;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def init_state() -> None:
    st.session_state.setdefault("audio_path", None)
    st.session_state.setdefault("segments", [])
    st.session_state.setdefault("parts", [])
    st.session_state.setdefault("analysis_path", None)
    st.session_state.setdefault("exports", [])


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def save_uploaded_file(uploaded, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / safe_filename(uploaded.name)
    if target.exists():
        target = directory / f"{target.stem}_{len(list(directory.glob(target.stem + '*')))}{target.suffix}"
    target.write_bytes(uploaded.getbuffer())
    return target


def parts_dataframe(parts) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Inclure": False,
                "#": part.index,
                "Début": format_time(part.start),
                "Fin": format_time(part.end),
                "Titre": part.title,
                "Description": part.description,
            }
            for part in parts
        ]
    )


def selected_parts_from_editor(edited: pd.DataFrame):
    parts = st.session_state.parts
    selected = []
    if edited is None or edited.empty:
        return selected
    for _, row in edited[edited["Inclure"]].iterrows():
        index = int(row["#"]) - 1
        if 0 <= index < len(parts):
            selected.append(parts[index])
    return selected


def update_analysis_progress(message: str, status, progress_bar, progress_text) -> None:
    status.info(message)
    progress_text.caption(message)
    if "Chargement du modèle" in message:
        progress_bar.progress(0.02)
    elif "Transcription en cours" in message:
        progress_bar.progress(0.05)
    match = re.search(r"Transcription:\s*([0-9:.]+)\s*/\s*([0-9:.]+)", message)
    if match:
        try:
            current = parse_time(match.group(1))
            total = parse_time(match.group(2))
            if total > 0:
                progress_bar.progress(min(current / total, 1.0))
        except ValueError:
            pass


init_state()

st.title(APP_TITLE)
st.caption(f"Version web Streamlit {APP_VERSION}")

with st.sidebar:
    st.markdown("### Menu")
    page = st.radio(
        "Navigation",
        ["Analyse", "Export", "Audios générés", "Help"],
        label_visibility="collapsed",
    )
    st.divider()
    st.header("Sources")
    uploaded_audio = st.file_uploader(
        "Uploader un audio",
        type=["aac", "m4a", "mp3", "wav", "ogg", "flac", "opus"],
    )
    model_name = st.selectbox("Modèle Whisper", ["tiny", "base", "small", "medium"], index=0)
    st.caption("En ligne, commence par `tiny`. Les modèles plus grands peuvent dépasser les ressources gratuites.")
    language = st.text_input("Langue", value="fr")
    cloud_safe_mode = st.checkbox("Mode cloud sécurisé", value=env_flag("DRSM_CLOUD_SAFE_DEFAULT", True))
    cloud_limit_default = max(1, min(env_int("DRSM_CLOUD_LIMIT_MINUTES", 3), 60))
    cloud_file_limit_mb = env_float("DRSM_CLOUD_MAX_UPLOAD_MB", 50.0)
    cloud_limit_minutes = st.number_input(
        "Limite analyse cloud (minutes)",
        min_value=1,
        max_value=60,
        value=cloud_limit_default,
        disabled=not cloud_safe_mode,
    )

    if uploaded_audio is not None:
        if st.button("Utiliser cet audio"):
            path = save_uploaded_file(uploaded_audio, UPLOADS_DIR)
            st.session_state.audio_path = str(path)
            st.session_state.segments = []
            st.session_state.parts = []
            st.session_state.analysis_path = None
            st.success(f"Audio chargé: {path.name}")

    st.divider()
    st.header("Analyses")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    analyses = sorted(ANALYSIS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    analysis_choice = st.selectbox(
        "Charger une analyse existante",
        [""] + [path.name for path in analyses],
    )
    if st.button("Charger analyse") and analysis_choice:
        path = ANALYSIS_DIR / analysis_choice
        audio_path, segments, parts = load_analysis(path)
        st.session_state.audio_path = str(audio_path)
        st.session_state.segments = segments
        st.session_state.parts = parts
        st.session_state.analysis_path = str(path)
        st.success(f"Analyse chargée: {path.name}")

    st.divider()
    st.header("Exports")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_sidebar_exports = sorted(set([Path(p) for p in st.session_state.exports] + list(EXPORTS_DIR.glob("*.wav"))))
    if all_sidebar_exports:
        st.caption(f"{len(all_sidebar_exports)} fichier(s) généré(s)")
        for export_path in all_sidebar_exports[-5:]:
            st.write(export_path.name)
    else:
        st.caption("Aucun export")

audio_path = Path(st.session_state.audio_path) if st.session_state.audio_path else None

if page == "Analyse":
    st.header("Analyse audio")
    if audio_path:
        col_audio, col_meta = st.columns([2, 1], vertical_alignment="top")
        with col_audio:
            st.markdown(f"**Audio courant**  \n`{audio_path}`")
            if audio_path.exists():
                try:
                    st.audio(str(audio_path))
                except Exception:
                    pass
            else:
                st.warning("Le chemin audio de l'analyse n'existe pas dans cet environnement.")
        with col_meta:
            if audio_path.exists():
                size_mb = audio_path.stat().st_size / (1024 * 1024)
                st.metric("Taille", f"{size_mb:.1f} Mo")
                try:
                    if cloud_safe_mode and size_mb > cloud_file_limit_mb:
                        st.warning("Mode cloud: fichier trop lourd pour une analyse fiable en ligne.")
                    else:
                        current_duration = audio_duration(audio_path)
                        st.metric("Durée", format_time(current_duration))
                        if cloud_safe_mode and current_duration > cloud_limit_minutes * 60:
                            st.warning(
                                f"Mode cloud: limite {cloud_limit_minutes} min. "
                                "Cet audio est trop long pour l'analyse en ligne."
                            )
                except Exception as exc:
                    st.metric("Durée", "inconnue")
                    with st.expander("Erreur lecture durée"):
                        st.exception(exc)
            if st.session_state.analysis_path:
                st.caption(f"Analyse: `{Path(st.session_state.analysis_path).name}`")
    else:
        st.info("Charge un fichier audio dans la barre latérale.")

    if audio_path and st.button("Analyser avec Whisper", type="primary"):
        if not audio_path.exists():
            st.error("Le fichier audio est introuvable. Recharge l'audio.")
        else:
            model_for_analysis = model_name
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            if cloud_safe_mode and model_name != "tiny":
                st.warning("Mode cloud sécurisé: le modèle `tiny` est utilisé pour éviter un redémarrage serveur.")
                model_for_analysis = "tiny"
            if cloud_safe_mode and size_mb > cloud_file_limit_mb:
                st.error(
                    "Analyse bloquée en mode cloud sécurisé: "
                    f"fichier de {size_mb:.0f} Mo pour une limite recommandée de {cloud_file_limit_mb:.0f} Mo."
                )
                st.info("Pour un cours complet, utilise la version desktop locale ou découpe d'abord un extrait court.")
                st.stop()
            try:
                duration = audio_duration(audio_path)
            except Exception as exc:
                if cloud_safe_mode:
                    st.error("Analyse bloquée: impossible de lire la durée audio en mode cloud sécurisé.")
                    with st.expander("Détails techniques"):
                        st.exception(exc)
                    st.stop()
                duration = 0.0
            if cloud_safe_mode and duration > cloud_limit_minutes * 60:
                st.error(
                    "Analyse bloquée en mode cloud sécurisé: "
                    f"audio de {format_time(duration)} pour une limite de {cloud_limit_minutes} min."
                )
                st.info(
                    "Sur Streamlit Cloud gratuit, les longs cours font souvent redémarrer l'app. "
                    "Utilise la version desktop pour l'audio complet, ou teste en ligne avec un extrait court."
                )
                st.stop()

            status = st.empty()
            progress_bar = st.progress(0.0)
            progress_text = st.empty()
            progress_messages = []

            def report(message: str) -> None:
                progress_messages.append(message)
                update_analysis_progress(message, status, progress_bar, progress_text)

            try:
                segments = transcribe_audio(audio_path, model_for_analysis, language.strip(), report)
                progress_bar.progress(1.0)
                parts = segment_course(segments)
                analysis_path = save_analysis(audio_path, segments, parts)
                st.session_state.segments = segments
                st.session_state.parts = parts
                st.session_state.analysis_path = str(analysis_path)
                status.success(f"Analyse terminée: {analysis_path.name}")
            except Exception as exc:
                progress_bar.empty()
                status.error(
                    "L'analyse a échoué. Sur Streamlit Cloud, utilise le modèle `tiny` "
                    "et teste avec un extrait plus court si l'audio est long."
                )
                with st.expander("Détails techniques"):
                    st.exception(exc)

    if st.session_state.parts:
        st.subheader("Parties détectées")
        st.dataframe(
            parts_dataframe(st.session_state.parts).drop(columns=["Inclure"]),
            use_container_width=True,
            hide_index=True,
            height=560,
        )
        with st.expander("Transcription complète"):
            st.write("\n\n".join(part.transcript for part in st.session_state.parts))

elif page == "Export":
    st.header("Sélection et export")
    if not st.session_state.parts:
        st.info("Analyse ou charge d'abord une analyse.")
    else:
        col_table, col_panel = st.columns([3, 1.25], gap="large")
        with col_table:
            st.caption("Coche une ou plusieurs parties. La sélection est exportée dans l'ordre du cours.")
            edited = st.data_editor(
                parts_dataframe(st.session_state.parts),
                use_container_width=True,
                hide_index=True,
                disabled=["#", "Début", "Fin", "Titre", "Description"],
                key="parts_editor",
                height=650,
                column_config={
                    "Inclure": st.column_config.CheckboxColumn("Inclure", width="small"),
                    "#": st.column_config.NumberColumn("#", width="small"),
                    "Début": st.column_config.TextColumn("Début", width="small"),
                    "Fin": st.column_config.TextColumn("Fin", width="small"),
                    "Titre": st.column_config.TextColumn("Titre", width="large"),
                    "Description": st.column_config.TextColumn("Description", width="large"),
                },
            )
        selected_parts = selected_parts_from_editor(edited)
        with col_panel:
            st.subheader("Export")
            if selected_parts:
                total_duration = sum(part.end - part.start for part in selected_parts)
                st.metric("Parties", len(selected_parts))
                st.metric("Durée", format_time(total_duration))
                suggested_title = export_title_for(selected_parts)
                title = st.text_input(
                    "Titre export",
                    value=suggested_title,
                    key=f"export_title_{'_'.join(str(p.index) for p in selected_parts)}",
                )
                with st.expander("Sélection", expanded=True):
                    for part in selected_parts:
                        st.write(f"**{part.index}.** {format_time(part.start)}-{format_time(part.end)}")
                        st.caption(part.title)
                if len(selected_parts) == 1:
                    st.caption("Ajuste la plage si besoin.")
                    start_value = st.text_input("Début", value=format_time(selected_parts[0].start))
                    end_value = st.text_input("Fin", value=format_time(selected_parts[0].end))
                    ranges = [(parse_time(start_value), parse_time(end_value))]
                else:
                    ranges = [(part.start, part.end) for part in selected_parts]
                    st.info("Les parties seront concaténées.")

                if st.button("Générer WAV", type="primary", use_container_width=True):
                    if not audio_path or not audio_path.exists():
                        st.error("Audio original introuvable.")
                    else:
                        indexes = "_".join(f"{part.index:02d}" for part in selected_parts[:6])
                        suffix = "_etc" if len(selected_parts) > 6 else ""
                        output = EXPORTS_DIR / f"{safe_filename(title)}_{indexes}{suffix}.wav"
                        export_clips(audio_path, output, ranges)
                        st.session_state.exports.append(str(output))
                        st.success(f"Export créé: {output.name}")
                        st.audio(str(output))
                        st.download_button(
                            "Télécharger le WAV",
                            data=output.read_bytes(),
                            file_name=output.name,
                            mime="audio/wav",
                            use_container_width=True,
                        )
            else:
                st.info("Coche une ou plusieurs parties à exporter.")

elif page == "Audios générés":
    st.header("Audios générés")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_exports = sorted(set([Path(p) for p in st.session_state.exports] + list(EXPORTS_DIR.glob("*.wav"))))
    if not all_exports:
        st.info("Aucun export pour l'instant.")
    else:
        col_list, col_player = st.columns([1.2, 2], gap="large")
        with col_list:
            export_table = pd.DataFrame(
                [
                    {
                        "Nom": path.name,
                        "Durée": format_time(audio_duration(path)),
                        "Chemin": str(path),
                    }
                    for path in all_exports
                ]
            )
            st.dataframe(export_table[["Nom", "Durée"]], use_container_width=True, hide_index=True, height=420)
            choice = st.selectbox("Choisir un export", all_exports, format_func=lambda p: p.name)
        with col_player:
            duration = audio_duration(choice)
            st.subheader(choice.name)
            st.audio(str(choice))
            meta1, meta2 = st.columns(2)
            meta1.metric("Durée", format_time(duration))
            meta2.download_button(
                "Télécharger",
                data=choice.read_bytes(),
                file_name=choice.name,
                mime="audio/wav",
                use_container_width=True,
            )

            st.divider()
            st.subheader("Créer un sous-audio")
            col1, col2, col3 = st.columns([1, 1, 2])
            sub_start = col1.text_input("Début sous-audio", value="00:00")
            sub_end = col2.text_input("Fin sous-audio", value=format_time(duration))
            sub_title = col3.text_input("Titre sous-audio", value=f"{choice.stem}_extrait")
            if st.button("Exporter sous-audio", type="primary"):
                output = EXPORTS_DIR / f"{safe_filename(sub_title)}.wav"
                export_clips(choice, output, [(parse_time(sub_start), parse_time(sub_end))])
                st.session_state.exports.append(str(output))
                st.success(f"Sous-audio créé: {output.name}")
                st.audio(str(output))
                st.download_button(
                    "Télécharger le sous-audio",
                    data=output.read_bytes(),
                    file_name=output.name,
                    mime="audio/wav",
                )

else:
    st.header("Help")
    st.subheader("Diagnostic")
    diag = {
        "Python": sys.version.split()[0],
        "Plateforme": platform.platform(),
        "Dossier app": str(Path(__file__).resolve().parent),
        "Dossier travail": str(WORK_DIR),
        "Mode cloud par défaut": env_flag("DRSM_CLOUD_SAFE_DEFAULT", True),
        "STREAMLIT_SHARING": os.environ.get("STREAMLIT_SHARING", ""),
        "STREAMLIT_RUNTIME_ENV": os.environ.get("STREAMLIT_RUNTIME_ENV", ""),
    }
    st.json(diag)
    st.dataframe(pd.DataFrame(dependency_status()), use_container_width=True, hide_index=True)
    if st.button("Tester imports audio lourds"):
        try:
            import_av()
            import_whisper_model_class()
            st.success("Imports PyAV et faster-whisper OK.")
        except Exception as exc:
            st.error("Un import audio a échoué.")
            st.exception(exc)

    st.divider()
    readme = Path(__file__).with_name("README.md")
    if readme.exists():
        st.markdown(readme.read_text(encoding="utf-8"))
    else:
        st.write(f"{APP_TITLE} {APP_VERSION}")
