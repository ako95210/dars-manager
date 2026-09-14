# Plan général vers la bêta cloud

Date de départ : 9 septembre 2026.
Objectif : mise à disposition d'une bêta privée à un client le 26 octobre 2026.

## Avancement

- Lot 0 : terminé le 9 septembre 2026.
- Lot 1 : terminé le 9 septembre 2026.
  - rôles client/administrateur : terminé ;
  - catalogue de tarifs et registre de consommation : terminé ;
  - relevé client et saisie administrative des paiements : première version terminée ;
  - état complet des jobs dans PostgreSQL : terminé ;
  - worker séparé, file Redis, polling PostgreSQL, baux et tentatives : terminé ;
  - limitation de concurrence : un processus worker par défaut, quotas fins à terminer.
- Lot 2 : terminé le 9 septembre 2026.
  - contrat `MediaStorage` local/S3 : terminé ;
  - réservation d'asset, upload direct et validation de taille : terminé ;
  - lancement d'un job depuis un asset : terminé ;
  - artefacts de worker stockés hors du disque API : terminé ;
  - empreintes SHA-256 des sources et artefacts : terminé ;
  - purge planifiée indépendante et suppression immédiate : terminé ;
  - mesure cumulative du stockage et écriture dans le registre : terminé.
- Lot 3 : terminé le 14 septembre 2026.
- Lot 4 : terminé le 14 septembre 2026.
  - lecteur audio et ouverture de l'analyse depuis le stockage objet : terminé ;
  - édition durable des titres, descriptions et timestamps : terminé ;
  - contrôle de concurrence par empreinte et recalcul des transcriptions : terminé ;
  - sélection/concaténation audio par job enfant durable : terminé ;
  - bibliothèque `BrandKit`, import PNG/JPEG/MP4/MOV et vignettes : terminé ;
  - modes image extraite/fond animé et choix du format dans l'atelier : terminé ;
  - zones dynamiques titre/intervenant/date/épisode : terminé ;
  - rendus worker 16:9, 1:1 et 9:16, fixes ou animés : terminé ;
  - archive `.dars` contrôlée et réimport sans retranscription : terminé.

## État de départ

Déjà validé ou présent dans le dépôt :

- pipeline transcription, segmentation, export audio, couverture et vidéo ;
- API FastAPI et interface React responsive ;
- authentification par mot de passe et session ;
- isolation initiale des projets par utilisateur ;
- PostgreSQL, Redis et migrations Alembic ;
- upload, progression, pause, annulation et téléchargement ;
- premiers écrans d'édition/suppression de projet et de récupération des jobs.

Le pipeline actuel s'exécute encore dans un processus enfant de l'API. L'état
métier des jobs est durable dans PostgreSQL, mais un redémarrage de l'API marque
encore le traitement comme interrompu. Le frontend web ne reprend pas encore
toutes les fonctions d'édition de l'application historique.

## Périmètre fonctionnel de la bêta

Le client pilote doit pouvoir :

1. se connecter à son espace privé ;
2. créer, modifier, ouvrir et supprimer un projet ;
3. envoyer un cours audio et fermer la page sans arrêter le job ;
4. retrouver les traitements et leurs erreurs depuis le dashboard ;
5. transcrire et segmenter le cours dans le cloud ;
6. consulter les parties, modifier titres et timestamps, puis sélectionner une
   ou plusieurs parties ;
7. écouter le résultat et produire un export audio ;
8. générer une couverture et une vidéo à image fixe ;
9. fournir et réutiliser son propre template de couverture ;
10. télécharger chaque résultat ou une archive complète `.dars` ;
11. connaître la date d'expiration des fichiers cloud et les supprimer ;
12. consulter le coût estimé puis confirmé de chaque traitement ;
13. consulter son total mensuel et ses paiements manuels enregistrés ;
14. voir un résumé d'impact privé : cours terminés, heures produites, stockage et
    coût sur la semaine.

L'administrateur doit pouvoir créer les comptes, consulter les consommations,
enregistrer un paiement et rapprocher le total interne des factures cloud.

## Agenda

### Lot 0 — Stabilisation, du 9 au 11 septembre

- terminer l'édition/suppression des projets ;
- rendre la liste et la reprise visuelle des jobs fiables ;
- ajouter les tests API et les styles manquants ;
- supprimer les incohérences connues et obtenir une base de tests verte ;
- valider et versionner les documents d'architecture et de planning.

Sortie : dépôt propre, comportement actuel démontrable et base de départ figée.

### Lot 1 — Persistance cloud, du 14 au 18 septembre

- faire de PostgreSQL la source de vérité des jobs et artefacts ;
- ajouter les rôles client/administrateur ;
- créer le socle financier permanent : catalogue de tarifs versionné, événements
  de consommation immuables, paiements et périodes de relevé ;
- introduire `CostRecorder` avant tout branchement à un service payant ;
- séparer le worker du processus API ;
- mettre en place les reprises, tentatives et erreurs durables ;
- limiter la concurrence par client et globalement ;
- introduire les interfaces `JobQueue` et `MediaStorage`.

Sortie : fermer ou redémarrer l'interface/API ne fait plus perdre un job, et le
schéma financier est prêt avant la première dépense cloud.

### Lot 2 — Médias temporaires, du 21 au 25 septembre

- brancher un stockage objet compatible S3 ;
- envoyer les fichiers directement avec des autorisations temporaires ;
- contrôler formats, taille, empreinte et propriété ;
- appliquer automatiquement les expirations ;
- mesurer les octets et leur durée de conservation pour alimenter le registre ;
- afficher la date de suppression et permettre la suppression immédiate.

Sortie : aucun média de production ne dépend du disque éphémère de l'API.

### Lot 3 — Traitement cloud, du 28 septembre au 2 octobre

État : **clôturé le 14 septembre 2026**. L'intégration a été validée sur les dix
premières minutes d'un cours réel. L'interface fournisseur,
l'estimation préalable, le découpage WAV mono 16 kHz, la fusion des timestamps,
les checkpoints de reprise et les écritures de coût par appel sont implémentés.
La chaîne a produit 85 segments, 3 parties et tous les artefacts attendus pour
un coût calculé de 0,0601 USD. La comparaison avec le modèle local `base`
confirme le choix de `whisper-1` pour la Beta : transcription plus cohérente et
4,56 fois plus rapide sur le cours de référence.

- ajouter `TranscriptionProvider` et le fournisseur managé initial ;
- calculer et afficher une estimation avant le lancement ;
- extraire, compresser et découper les cours longs ;
- conserver les timestamps lors de la fusion des fragments ;
- enregistrer chaque appel facturable avec son identifiant fournisseur ;
- rendre les tentatives et écritures idempotentes pour éviter les doubles coûts ;
- exécuter segmentation et rendus dans les workers ;
- comparer un cours de référence au résultat validé en phase 1.

Sortie : un cours long traverse le pipeline cloud complet, reste récupérable et
son coût peut être expliqué appel par appel.

### Lot 4 — Travail éditorial et archive, du 5 au 9 octobre

État : **clôturé le 14 septembre 2026**. Le parcours éditorial complet est
disponible dans l'interface web : correction des parties, exports audio,
templates image/vidéo versionnés, zones dynamiques et rendus multi-formats. Une
archive `.dars` rassemble l'analyse corrigée, l'audio et les derniers rendus
avec taille et empreinte SHA-256 par fichier. Son réimport restaure directement
un cours éditable sans appeler le fournisseur de transcription.

- afficher et éditer les parties et timestamps ;
- sélectionner et concaténer les passages ;
- ajouter le lecteur audio web et les sous-extraits ;
- créer le `BrandKit` et permettre l'import de templates PNG/JPEG ;
- positionner les zones dynamiques du titre, de l'intervenant et de la date ;
- prévisualiser puis générer les formats 16:9, 1:1 et 9:16 disponibles ;
- conserver la version du template utilisée dans chaque rendu ;
- générer les artefacts finaux et l'archive `.dars` ;
- réimporter une archive sans retranscrire inutilement.

Sortie : parité sur le parcours principal de l'application historique.

### Lot 5 — Coûts et relevés, du 12 au 16 octobre

- finaliser la réconciliation entre coûts mesurés et factures fournisseurs ;
- afficher le détail par projet et le cumul mensuel ;
- ajouter budgets, avertissements et seuil de confirmation ;
- fournir le dashboard administrateur ;
- enregistrer les paiements manuels et exporter un relevé CSV/PDF ;
- générer le résumé d'impact hebdomadaire privé à partir des mêmes écritures ;
- préparer les entités futures de contribution et d'allocation sans checkout.

Sortie : chaque montant affiché est explicable par des écritures auditables.

### Lot 6 — Sécurité et livraison, du 19 au 23 octobre

- déployer l'environnement bêta derrière HTTPS ;
- isoler les secrets et les comptes fournisseurs ;
- vérifier l'autorisation sur chaque ressource ;
- tester sauvegarde et restauration de PostgreSQL ;
- ajouter supervision, journaux techniques et alertes ;
- effectuer les tests de charge, d'expiration et de panne ;
- rédiger le parcours d'accueil du client pilote.

Sortie : décision go/no-go documentée.

### Bêta pilote, à partir du 26 octobre

- ouverture au premier client ;
- suivi quotidien la première semaine ;
- correction des défauts bloquants ;
- collecte des mesures réelles de durée, stockage et coût ;
- bilan et priorisation avant l'ajout de nouveaux clients.

## Après la bêta

Ordre proposé :

1. stabilisation et ouverture à trois clients ;
2. connecteurs de publication avec validation humaine ;
3. rapport d'impact hebdomadaire et page publique ;
4. campagnes et contributions communautaires ;
5. paiement en ligne, remboursements et rapprochement automatique ;
6. automatisation contrôlée de la publication ;
7. éventuel mode local ou synchronisation comme option premium/confidentielle.

## Règles de pilotage

- aucune nouvelle fonction ne contourne l'isolation multi-client ;
- aucune dépense cloud n'est lancée sans mesure et attribution ;
- les estimations sont distinguées des coûts confirmés ;
- les traitements sont idempotents et les retries bornés ;
- chaque lot se termine par tests, démonstration et commit dédié ;
- toute extension non indispensable à la bêta rejoint le backlog après le lot 6.

## Risques principaux

- gros fichiers et connexions lentes : upload direct, reprise et compression ;
- limite des fournisseurs : fragmentation et abstraction du prestataire ;
- double facturation lors des retries : clés d'idempotence et registre immuable ;
- perte de médias : statut d'archive visible et expiration explicite ;
- dérive des coûts : budgets, plafonds et alertes ;
- régression fonctionnelle : cours de référence et tests de bout en bout ;
- dépendance fournisseur : contrats internes et exports portables.
