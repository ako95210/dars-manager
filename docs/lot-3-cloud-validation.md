# Validation du traitement cloud — Lot 3

Date : 11 septembre 2026

## Périmètre

Le test utilise les dix premières minutes d'un cours réel de 31 min 06 s. Le
contenu transcrit, la clé API et les identifiants fournisseur ne sont pas
conservés dans Git.

- fournisseur : OpenAI ;
- modèle : `whisper-1` ;
- langue demandée : français ;
- entrée fournisseur : WAV mono, 16 kHz ;
- découpage : un fragment de neuf minutes puis un fragment d'une minute ;
- réponse : `verbose_json` avec timestamps par segment.

## Résultats

| Mesure | Résultat |
| --- | ---: |
| Durée source testée | 599,932 s |
| Appels fournisseur | 2 |
| Identifiants de requête reçus | 2 |
| Segments transcrits | 85 |
| Parties détectées | 3 |
| Premier timestamp | 0,000 s |
| Dernier timestamp | 599,932 s |
| Temps de transcription | 28,984 s |
| Secondes facturables estimées | 601 s |
| Coût calculé au tarif enregistré | 0,0601 USD |
| Temps du rendu local aval | 14,508 s |

Les timestamps sont monotones. Aucun segment n'est vide, aucun couple de
segments ne se chevauche et aucune répétition n'apparaît à la jonction des deux
fragments.

## Artefacts validés

- `analysis.json` : schéma 3, 19 431 octets ;
- audio WAV : 38 395 754 octets, 599,932 s ;
- couverture PNG : 1 280 × 720, 26 686 octets ;
- vidéo MP4 : 9 895 572 octets, 599,953 s.

La copie source limitée à dix minutes a été supprimée après validation. Les
artefacts du test restent uniquement dans le stockage volatile `/dev/shm` et ne
font pas partie du dépôt.

## Conclusion

Le chemin critique du lot 3 est validé sur un média réel : normalisation,
découpage, transcription cloud horodatée, fusion, segmentation et génération
des quatre artefacts. La comparaison éditoriale détaillée avec la transcription
locale de phase 1 reste à effectuer avant de déclarer le lot entièrement clos.
