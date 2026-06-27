# Dars Manager (`drsm`)

Application locale pour analyser un cours audio, le découper en parties avec timestamps, puis exporter une ou plusieurs parties choisies en fichier audio indépendant.

Les analyses sont sauvegardées dans le répertoire de travail local.

Par défaut:

```text
./work/analyses
```

Sur Mac avec l'installateur simplifié:

```text
~/Documents/DarsManager/analyses
```

Ce répertoire peut être remplacé par la variable d'environnement `DRSM_WORK_DIR`.

## Lancer En Local

### macOS

Pour un utilisateur Mac, le mode recommandé est l'application web locale. Les fichiers restent sur la machine de l'utilisateur.

Le package le plus confortable est un `.pkg` construit sur macOS:

```bash
./build_macos_pkg.sh 1.3
```

Pour creer directement un zip de livraison contenant le `.pkg` et une notice simple:

```bash
./package_macos_delivery.sh 1.3
```

Le fichier a transmettre a l'utilisateur sera cree dans `dist/`, par exemple:

```text
dist/dars-manager-v1.3-mac-livraison.zip
```

Le package installe:

- `/Applications/DarsManager`: fichiers de l'application;
- `/Applications/Dars Manager.app`: lanceur utilisateur;
- `Dars Manager.command` sur le Bureau.

Si tu as un certificat Apple Developer ID Installer, tu peux signer:

```bash
MACOS_INSTALLER_CERT='Developer ID Installer: ...' ./build_macos_pkg.sh 1.3
```

Avec les variables Apple de notarisation, le script peut aussi soumettre et stapler le package.

Après installation, cette commande doit afficher `Library/Application Support`:

```bash
grep VENV_DIR "/Applications/DarsManager/drsm_mac.command"
```

Procédure simplifiée:

1. Télécharge le projet depuis GitHub.
2. Double-clique sur `install_macos.command`.
3. Si macOS bloque l'ouverture, fais clic droit sur `install_macos.command`, puis **Ouvrir**.
4. Ensuite, lance l'application avec le raccourci `Dars Manager.command` créé sur le Bureau.

Fallback Terminal:

```bash
xattr -dr com.apple.quarantine .
chmod +x install_macos.command drsm_mac.command
./install_macos.command
```

Si macOS dit que `install_macos.command` est endommagé, ne le mets pas à la corbeille: enlève la quarantaine avec la commande `xattr` ci-dessus.

Si Python 3 manque, l'installateur ouvre la page officielle de téléchargement Python. Il suffit d'installer Python, puis de relancer `install_macos.command`.

Le premier lancement installe les dépendances dans `.venv`. Cela peut prendre plusieurs minutes.

Mode d'emploi détaillé:

```text
MODE_D_EMPLOI_MAC.md
```

Les données locales sont stockées ici:

```text
~/Documents/DarsManager
```

L'environnement Python local est stocké ici:

```text
~/Library/Application Support/DarsManager/.venv
```

Ce dossier contient:

- `uploads`: audios chargés;
- `analyses`: analyses sauvegardées;
- `exports`: audios générés;
- `hf_cache`: cache des modèles Whisper.

### Windows

Pour un utilisateur Windows, le mode recommandé est aussi l'application web locale.

Procédure simplifiée:

1. Télécharge le ZIP Windows.
2. Dézippe le fichier.
3. Ouvre `INSTALLATION_WINDOWS.txt`.
4. Double-clique sur `install_windows.bat`.
5. Ensuite, lance l'application avec le raccourci `Dars Manager.bat` créé sur le Bureau.

Si Python 3 manque, l'installateur ouvre la page officielle de téléchargement Python. Il suffit d'installer Python, puis de relancer `install_windows.bat`.

Les données locales sont stockées ici:

```text
Documents\DarsManager
```

Ce dossier contient les audios chargés, les analyses sauvegardées, les exports et le cache Whisper.

### Packaging Livraison

Créer un ZIP Mac depuis le dernier tag:

```bash
./delivery.sh mac
```

Créer un ZIP Windows depuis le dernier tag:

```bash
./delivery.sh windows
```

Créer un ZIP depuis un tag précis:

```bash
./delivery.sh mac v0.2
./delivery.sh windows v0.3
```

### Linux

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
/home/ayoub/dev/perso/dars-manager/drsm_local.sh
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

Le fichier `runtime.txt` force Python 3.10, proche de l'environnement local utilisé pour développer l'application. Sans ce fichier, Streamlit Cloud peut choisir une version Python plus récente qui casse certaines dépendances audio.

La version Streamlit couvre la V1 diffusable:

- upload audio;
- analyse Whisper;
- pause, reprise et annulation d'analyse;
- chargement d'analyses;
- affichage des parties;
- sélection multi-parties;
- export WAV;
- lecture avec `st.audio`;
- téléchargement des exports;
- sous-audio depuis un export.

Limite actuelle de la version web: le lecteur avancé, l'enregistrement micro et la correction par prise voix restent dans la version desktop.

Sur Streamlit Cloud, garde le modèle Whisper `tiny`. Les modèles `base`, `small` et `medium` peuvent dépasser les ressources gratuites, surtout avec des cours longs.

Le **mode cloud sécurisé** bloque volontairement l'analyse des audios trop longs, par défaut au-delà de 3 minutes ou 50 Mo. Pour analyser un cours complet d'une heure, utilise plutôt la version desktop locale.

Si l'app plante encore en ligne, ouvre l'onglet **Help** puis regarde le bloc **Diagnostic**. Il permet de vérifier la version Python et les paquets audio installés sans lancer une transcription.

## Déploiement Réel Avec Docker

Pour analyser de vrais cours longs, la cible recommandée est un serveur avec Docker et un volume persistant. Streamlit Community Cloud est adapté à une démo courte, pas à une transcription Whisper complète.

Lancer localement avec Docker Compose:

```bash
cd /home/ayoub/dev/perso/dars-manager
docker compose up --build
```

Puis ouvre:

```text
http://localhost:8501
```

Dans ce mode:

- le mode cloud sécurisé est désactivé par défaut;
- les uploads, analyses, exports et caches Whisper sont stockés dans le dossier local `./work`;
- le répertoire de travail dans le conteneur est `/data`;
- `HF_HOME=/data/hf_cache` évite de retélécharger le modèle Whisper à chaque redémarrage.

Correspondance des dossiers:

```text
./work/uploads   -> /data/uploads
./work/analyses  -> /data/analyses
./work/exports   -> /data/exports
./work/hf_cache  -> /data/hf_cache
```

Variables utiles:

```bash
DRSM_WORK_DIR=/data
DRSM_CLOUD_SAFE_DEFAULT=false
DRSM_CLOUD_LIMIT_MINUTES=60
DRSM_CLOUD_MAX_UPLOAD_MB=1024
```

Sur un VPS, les commandes minimales sont:

```bash
git clone https://github.com/ako95210/dars-manager.git
cd dars-manager
docker compose up -d --build
```

Ensuite expose le port `8501` derrière un reverse proxy HTTPS, par exemple Nginx ou Caddy.

Pour un service managé, il faut choisir une offre avec disque persistant ou volume attaché. Sans stockage persistant, les analyses et exports peuvent disparaître au redémarrage.

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
- Pause, reprise et annulation d'analyse sont prises en compte entre deux segments Whisper. Sur certains passages longs, la réaction peut donc prendre quelques secondes.
- Le découpage est automatique et heuristique. Les titres combinent un thème général et un angle spécifique pour différencier les parties d'un même sujet.
- Tu peux modifier les temps avant export.
- Tu peux modifier le titre proposé avant d'enregistrer l'audio exporté.
- L'export est en `.wav`, ce qui évite une dépendance à `ffmpeg` installé sur le système.
- La lecture intégrée utilise GStreamer via Python.
- La correction ponctuelle fonctionne sur les WAV générés par l'application.
- Les prises voix sont sauvegardées dans `/home/ayoub/dev/perso/dars-manager/work/recordings`.
