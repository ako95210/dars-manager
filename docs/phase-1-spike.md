# Phase 1 — Validation technique

Date du test : 8 septembre 2026.

## Objectif

Valider sur un cours réel le pipeline temporaire prévu pour la bêta :

1. chargement de l'audio dans un workspace volatile ;
2. transcription Faster Whisper dans un processus isolable ;
3. segmentation du cours ;
4. export WAV ;
5. génération d'une couverture 16:9 ;
6. rendu d'une vidéo MP4 à image fixe ;
7. téléchargement via API ;
8. suppression du workspace.

## Environnement du test

- CPU : AMD Ryzen 7 4700U, 8 CPU logiques ;
- mémoire : 38 Gio ;
- workspace : `/dev/shm/dars-manager-beta` ;
- modèle : Faster Whisper `base`, CPU `int8` ;
- threads Whisper : 4 ;
- durée du cours pilote : 01:02:53.

## Résultats

| Mesure | Résultat |
|---|---:|
| Durée totale du pipeline | 447,8 s (07:28) |
| Ratio global | environ 8,4 fois le temps réel |
| Segments Whisper | 1 456 |
| Parties détectées | 13 |
| Analyse JSON | 210 126 octets |
| Export WAV | 724 324 430 octets |
| Couverture PNG | 30 043 octets |
| Vidéo MP4 | 62 326 149 octets |

Le rendu en aval, exécuté avec une analyse existante, a pris 95,9 secondes. La
transcription et la segmentation représentent donc environ 5 minutes 52 secondes
du test complet.

## API validée

- `GET /api/health` ;
- création d'un job par upload multipart ;
- exécution dans un processus séparé ;
- progression structurée ;
- refus de l'accès depuis un autre identifiant utilisateur (`404`) ;
- téléchargement JSON, WAV, PNG et MP4 ;
- pause, reprise et annulation sur le cours complet ;
- suppression par `DELETE` ;
- job inaccessible après suppression (`404`).

Un extrait parlé d'une minute a terminé le pipeline HTTP en 6,05 secondes. Un
extrait silencieux n'a produit aucun segment et a été placé proprement en échec.

## Décisions confirmées

- Un worker CPU suffit pour la première bêta.
- Le nombre de threads Whisper doit être configurable ; la valeur bêta initiale
  sera 4.
- Les fichiers temporaires doivent être placés sur un montage `tmpfs` dédié.
- Le WAV intermédiaire est l'artefact le plus volumineux. Il faut dimensionner le
  workspace à au moins 1 Gio par heure de cours et limiter la concurrence.
- Les jobs et artefacts doivent rester génériques pour accueillir plus tard les
  rendus carré/vertical et la publication sociale.

## Points restant avant la phase 2

- ajouter le conteneur et sa limite `tmpfs` ;
- remplacer l'identité pilote par une authentification réelle ;
- déplacer l'état des jobs vers Redis ;
- ajouter PostgreSQL pour les utilisateurs et métadonnées ;
- définir la politique exacte d'expiration et de téléchargement ;
- automatiser le test d'intégration HTTP du worker.
