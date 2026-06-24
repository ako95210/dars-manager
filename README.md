# Dars Manager (`drsm`)

Application locale pour analyser un cours audio, le découper en parties avec timestamps, puis exporter une ou plusieurs parties choisies en fichier audio indépendant.

Les analyses sont sauvegardées dans le répertoire de travail:

```text
/home/ayoub/dev/perso/dars-manager/work/analyses
```

## Lancer

Version desktop Tkinter:

```bash
python3 /home/ayoub/dev/perso/dars-manager/dars_manager.py
```

ou:

```bash
/home/ayoub/dev/perso/dars-manager/drsm_desktop.sh
```

Version web Streamlit:

```bash
/home/ayoub/dev/perso/dars-manager/drsm_streamlit.sh
```

Puis ouvre:

```text
http://localhost:8501
```

Si le port `8501` est occupé:

```bash
cd /home/ayoub/dev/perso/dars-manager
streamlit run drsm_streamlit.py --server.port 8502
```

## Diffusion Streamlit Cloud

Pour publier rapidement:

1. Mets ce dossier dans un dépôt GitHub.
2. Dans Streamlit Community Cloud, choisis le dépôt.
3. Indique `drsm_streamlit.py` comme fichier principal.
4. Streamlit installera les dépendances depuis `requirements.txt`.

La version Streamlit couvre la V1 diffusable:

- upload audio;
- analyse Whisper;
- chargement d'analyses;
- affichage des parties;
- sélection multi-parties;
- export WAV;
- lecture avec `st.audio`;
- téléchargement des exports;
- sous-audio depuis un export.

Limite actuelle de la version web: le lecteur avancé, l'enregistrement micro et la correction par prise voix restent dans la version desktop.

## Utilisation

1. Clique sur **Choisir audio**.
2. Sélectionne un fichier `.aac`, `.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac` ou `.opus`.
3. Laisse le modèle sur `base` pour commencer, puis clique **Analyser**.
4. Sélectionne une partie dans la liste, ou plusieurs parties avec `Ctrl`/`Shift`.
5. Modifie **Titre export** si tu veux changer le nom proposé.
6. Pour une seule partie, ajuste les champs **Début** et **Fin** si besoin.
7. Clique **Exporter en WAV** pour créer un nouveau fichier audio indépendant.
8. Dans l'onglet **Audios générés**, sélectionne un export puis clique **Lire**, ou double-clique dessus.

Si plusieurs parties sont sélectionnées, elles sont exportées dans un seul fichier `.wav`, concaténées dans l'ordre du cours.

## Lecteur Et Édition Des Exports

L'onglet **Audios générés** contient:

- une liste des audios créés pendant la session;
- un bouton **Ajouter WAV** pour charger un export existant;
- lecture, pause, stop;
- avance/recul de 5 ou 10 secondes;
- changement de vitesse: `0.75x`, `1.0x`, `1.25x`, `1.5x`, `2.0x`;
- une barre de position pour se déplacer dans l'audio;
- un outil **Créer un sous-audio** depuis l'audio généré;
- une correction ponctuelle de plage par silence, par remplacement avec un autre fichier WAV, ou par une prise voix enregistrée depuis le micro.

Pour corriger avec ta voix:

1. Sélectionne l'audio généré à corriger.
2. Place **Début** et **Fin** dans **Correction ponctuelle d'une plage**.
3. Clique **Enregistrer micro**.
4. Dis la phrase de remplacement.
5. Clique **Arrêter**.
6. Utilise **Lire prise** pour vérifier.
7. Clique **Remplacer par prise** pour générer un nouveau WAV corrigé.

Le bouton **TTS** prépare le flux de travail pour remplacer une parole par du texte synthétisé, mais il nécessite un moteur local capable de générer un fichier audio. Cette machine n'en fournit pas actuellement.

## Reprendre une analyse

Clique sur **Charger analyse** et choisis un fichier `.json` dans le répertoire de travail. L'application recharge les parties et les timestamps sans refaire la transcription Whisper.

Si le fichier audio original a été déplacé, l'application te demandera de le retrouver pour pouvoir exporter de nouveaux extraits.

## Notes

- La première analyse peut être lente: Whisper travaille localement sur CPU.
- Le découpage est automatique et heuristique. Les titres combinent un thème général et un angle spécifique pour différencier les parties d'un même sujet.
- Tu peux modifier les temps avant export.
- Tu peux modifier le titre proposé avant d'enregistrer l'audio exporté.
- L'export est en `.wav`, ce qui évite une dépendance à `ffmpeg` installé sur le système.
- La lecture intégrée utilise GStreamer via Python.
- La correction ponctuelle fonctionne sur les WAV générés par l'application.
- Les prises voix sont sauvegardées dans `/home/ayoub/dev/perso/dars-manager/work/recordings`.
