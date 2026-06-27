# Installer Dars Manager sur Mac

## Installation

1. Ouvre le dossier `dars-manager`.
2. Double-clique sur `install_macos.command`.
3. Si macOS affiche une alerte, fais clic droit sur `install_macos.command`, puis clique sur **Ouvrir**.
4. Attends la fin de l'installation.

L'installation crée un raccourci sur le Bureau:

```text
Dars Manager.command
```

## Utilisation

Pour lancer Dars Manager, double-clique sur:

```text
Dars Manager.command
```

L'application s'ouvre dans le navigateur à l'adresse:

```text
http://localhost:8501
```

## Où sont les fichiers ?

Les fichiers restent sur le Mac.

Application:

```text
~/Applications/DarsManager
```

Données:

```text
~/Documents/DarsManager
```

Ce dossier contient les audios chargés, les analyses, les exports et le cache Whisper.

## En cas de problème

Si Python n'est pas installé, l'installateur ouvre la page de téléchargement Python. Installe Python, puis relance `install_macos.command`.

Si le raccourci du Bureau ne s'ouvre pas, fais clic droit dessus, puis clique sur **Ouvrir**.

Si le double-clic ne marche pas, ouvre Terminal dans le dossier `dars-manager`, puis lance:

```bash
chmod +x install_macos.command drsm_mac.command
./install_macos.command
```
