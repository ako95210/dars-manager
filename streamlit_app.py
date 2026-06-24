from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from audio_core import (
    ANALYSIS_DIR,
    APP_TITLE,
    APP_VERSION,
    DEFAULT_MODEL,
    EXPORTS_DIR,
    UPLOADS_DIR,
    audio_duration,
    export_clips,
    export_title_for,
    format_time,
    load_analysis,
    parse_time,
    safe_filename,
    save_analysis,
    segment_course,
    transcribe_audio,
)


st.set_page_config(page_title=APP_TITLE, layout="wide")


def init_state() -> None:
    st.session_state.setdefault("audio_path", None)
    st.session_state.setdefault("segments", [])
    st.session_state.setdefault("parts", [])
    st.session_state.setdefault("analysis_path", None)
    st.session_state.setdefault("exports", [])


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


init_state()

st.title(APP_TITLE)
st.caption(f"Version web Streamlit {APP_VERSION}")

with st.sidebar:
    st.header("Sources")
    uploaded_audio = st.file_uploader(
        "Uploader un audio",
        type=["aac", "m4a", "mp3", "wav", "ogg", "flac", "opus"],
    )
    model_name = st.selectbox("Modèle Whisper", ["tiny", "base", "small", "medium"], index=1)
    language = st.text_input("Langue", value="fr")

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
    for export_path in st.session_state.exports:
        st.write(Path(export_path).name)

audio_path = Path(st.session_state.audio_path) if st.session_state.audio_path else None

tab_analyze, tab_export, tab_generated, tab_help = st.tabs(
    ["Analyse", "Export", "Audios générés", "Help"]
)

with tab_analyze:
    st.subheader("Analyse audio")
    if audio_path:
        st.write(f"Audio courant: `{audio_path}`")
        if audio_path.exists():
            try:
                st.audio(str(audio_path))
                st.caption(f"Durée: {format_time(audio_duration(audio_path))}")
            except Exception:
                pass
        else:
            st.warning("Le chemin audio de l'analyse n'existe pas dans cet environnement.")
    else:
        st.info("Charge un fichier audio dans la barre latérale.")

    if audio_path and st.button("Analyser avec Whisper", type="primary"):
        if not audio_path.exists():
            st.error("Le fichier audio est introuvable. Recharge l'audio.")
        else:
            status = st.empty()
            progress_messages = []

            def report(message: str) -> None:
                progress_messages.append(message)
                status.info(message)

            segments = transcribe_audio(audio_path, model_name, language.strip(), report)
            parts = segment_course(segments)
            analysis_path = save_analysis(audio_path, segments, parts)
            st.session_state.segments = segments
            st.session_state.parts = parts
            st.session_state.analysis_path = str(analysis_path)
            status.success(f"Analyse terminée: {analysis_path.name}")

    if st.session_state.parts:
        st.subheader("Parties détectées")
        st.dataframe(
            parts_dataframe(st.session_state.parts).drop(columns=["Inclure"]),
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("Transcription complète"):
            st.write("\n\n".join(part.transcript for part in st.session_state.parts))

with tab_export:
    st.subheader("Sélection et export")
    if not st.session_state.parts:
        st.info("Analyse ou charge d'abord une analyse.")
    else:
        edited = st.data_editor(
            parts_dataframe(st.session_state.parts),
            use_container_width=True,
            hide_index=True,
            disabled=["#", "Début", "Fin", "Titre", "Description"],
            key="parts_editor",
        )
        selected_parts = selected_parts_from_editor(edited)
        if selected_parts:
            suggested_title = export_title_for(selected_parts)
            title = st.text_input("Titre export", value=suggested_title, key=f"export_title_{'_'.join(str(p.index) for p in selected_parts)}")
            if len(selected_parts) == 1:
                col1, col2 = st.columns(2)
                start_value = col1.text_input("Début", value=format_time(selected_parts[0].start))
                end_value = col2.text_input("Fin", value=format_time(selected_parts[0].end))
                ranges = [(parse_time(start_value), parse_time(end_value))]
            else:
                ranges = [(part.start, part.end) for part in selected_parts]
                st.caption("Plusieurs parties sélectionnées: elles seront concaténées dans l'ordre du cours.")

            if st.button("Générer WAV", type="primary"):
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
                    )
        else:
            st.info("Coche une ou plusieurs parties à exporter.")

with tab_generated:
    st.subheader("Audios générés")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_exports = sorted(set([Path(p) for p in st.session_state.exports] + list(EXPORTS_DIR.glob("*.wav"))))
    if not all_exports:
        st.info("Aucun export pour l'instant.")
    else:
        choice = st.selectbox("Choisir un export", all_exports, format_func=lambda p: p.name)
        st.audio(str(choice))
        st.caption(f"Durée: {format_time(audio_duration(choice))}")
        st.download_button("Télécharger", data=choice.read_bytes(), file_name=choice.name, mime="audio/wav")

        st.divider()
        st.subheader("Créer un sous-audio")
        col1, col2, col3 = st.columns([1, 1, 2])
        sub_start = col1.text_input("Début sous-audio", value="00:00")
        sub_end = col2.text_input("Fin sous-audio", value=format_time(audio_duration(choice)))
        sub_title = col3.text_input("Titre sous-audio", value=f"{choice.stem}_extrait")
        if st.button("Exporter sous-audio"):
            output = EXPORTS_DIR / f"{safe_filename(sub_title)}.wav"
            export_clips(choice, output, [(parse_time(sub_start), parse_time(sub_end))])
            st.session_state.exports.append(str(output))
            st.success(f"Sous-audio créé: {output.name}")
            st.audio(str(output))
            st.download_button("Télécharger le sous-audio", data=output.read_bytes(), file_name=output.name, mime="audio/wav")

with tab_help:
    st.subheader("README")
    readme = Path(__file__).with_name("README.md")
    if readme.exists():
        st.markdown(readme.read_text(encoding="utf-8"))
    else:
        st.write(f"{APP_TITLE} {APP_VERSION}")
