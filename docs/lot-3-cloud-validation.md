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

## Comparaison qualitative cloud/local

La même plage de 599,932 secondes a été transcrite localement avec
`faster-whisper` et le modèle `base`. Cette comparaison est relative : elle ne
remplace pas une transcription humaine de référence, mais les écarts
linguistiques et contextuels sont suffisamment nets pour choisir le moteur de
la Beta.

| Mesure | Cloud `whisper-1` | Local `base` |
| --- | ---: | ---: |
| Temps de transcription | 28,984 s | 132,317 s |
| Segments | 85 | 101 |
| Mots normalisés | 990 | 1 096 |
| Vocabulaire distinct | 348 | 419 |
| Fin du dernier segment | 599,932 s | 599,880 s |
| Segments avec ponctuation finale | 50,6 % | 46,5 % |
| Parties métier détectées | 3 | 1 |

La similarité séquentielle entre les deux textes est de 66,73 %. La sortie
locale est plus longue, mais cet écart provient en grande partie d'ajouts
phonétiques incohérents et non d'une meilleure couverture du cours.

Constats sur cinq fenêtres réparties dans l'extrait :

- le cloud conserve mieux la syntaxe française et les limites des phrases ;
- les noms de personnes, les références religieuses translittérées et les
  formules arabes sont globalement plus cohérents côté cloud ;
- la date prononcée est correctement restituée par le cloud, alors que le local
  altère notamment l'année ;
- le local génère davantage de mots, mais aussi plusieurs expressions sans sens
  dans leur contexte ;
- aucune duplication n'apparaît à la jonction cloud de 540 secondes ;
- deux répétitions exactes existent ailleurs dans la sortie cloud. Elles devront
  rester visibles et corrigeables dans l'éditeur du lot 4.

Le cloud est 4,56 fois plus rapide sur cette machine et permet au segmentateur
métier d'identifier trois parties cohérentes, contre une seule avec la sortie
locale. `whisper-1` est donc retenu comme moteur par défaut de la Beta. Le moteur
local reste disponible pour le développement et comme solution de secours.

## Conclusion et clôture

Le chemin critique du lot 3 est validé sur un média réel : normalisation,
découpage, transcription cloud horodatée, fusion, segmentation et génération
des quatre artefacts. La comparaison avec la transcription locale confirme le
choix du fournisseur cloud. Le lot 3 est clôturé le 14 septembre 2026 ; les deux
répétitions observées deviennent un cas de validation pour l'éditeur du lot 4.
