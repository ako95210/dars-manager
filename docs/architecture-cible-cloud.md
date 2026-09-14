# Architecture cible cloud

Statut : décision validée le 9 septembre 2026.

Ce document est la référence d'architecture de Dars Manager. Les documents de
phase précédents restent utiles comme historique des validations techniques,
mais leurs choix de déploiement local ne décrivent plus la cible produit.

## Principes validés

- Dars Manager est une application web cloud sans installation obligatoire.
- Le navigateur est l'interface unique sur Windows, macOS et Linux.
- Les traitements longs continuent lorsque le navigateur est fermé.
- Les médias ne sont conservés dans le cloud que pendant leur traitement et une
  période de restitution clairement affichée.
- L'utilisateur archive volontairement les résultats sur sa machine.
- Les comptes, projets, métadonnées, états de traitement et écritures de coût
  sont conservés sur le serveur.
- Chaque coût est attribué au client et au projet qui l'a généré.
- La bêta utilise des relevés mensuels et des paiements manuels enregistrés par
  un administrateur.
- Le registre financier doit pouvoir accueillir plus tard les contributions de
  la communauté, sans retour financier pour le contributeur.
- La publication sociale et les rapports d'impact utiliseront des connecteurs
  indépendants du coeur métier.

## Vue d'ensemble

```text
Navigateur React
      |
      v
API FastAPI -------- PostgreSQL
      |               comptes, projets, jobs, coûts, paiements
      |
      +-------------> stockage objet temporaire
      |               sources et résultats avec expiration
      |
      +-------------> file Redis
                          |
                          v
                    workers cloud
                    transcription, segmentation,
                    audio, image et vidéo
```

Les workers sont séparés de l'API web. Un redémarrage ou un déploiement de l'API
ne doit donc ni perdre ni interrompre silencieusement un traitement.

## Répartition des données

### Données permanentes du serveur

- utilisateurs, sessions, rôles et statut des comptes ;
- projets et métadonnées éditoriales ;
- identités visuelles et templates volontairement conservés par les clients ;
- jobs, progression, erreurs et historique ;
- catalogue versionné des tarifs ;
- événements de consommation immuables ;
- relevés, paiements manuels et corrections comptables ;
- métadonnées des archives et publications ;
- rapports d'impact agrégés.

### Données temporaires du cloud

- copie du média source ;
- fragments préparés pour les fournisseurs de transcription ;
- transcriptions de travail ;
- exports audio, images et vidéos ;
- paquet d'archive en attente de téléchargement.

La durée de conservation bêta par défaut est de sept jours après la fin du job.
Elle peut être prolongée jusqu'à une publication programmée ou écourtée par
l'action « Exporter et supprimer du cloud ».

### Données locales

Le fichier source original reste chez l'utilisateur. Les résultats sont
téléchargés séparément ou dans une archive `.dars`. Le navigateur ne doit pas
être considéré comme un stockage durable et aucun dossier synchronisé n'est
requis pour la bêta.

## Contrats d'extension

Le coeur métier dépend d'interfaces, et non d'un fournisseur particulier :

- `MediaStorage` pour le stockage objet ;
- `TranscriptionProvider` pour la reconnaissance vocale ;
- `JobQueue` pour l'exécution asynchrone ;
- `CostRecorder` pour la consommation ;
- `ArchiveBuilder` pour les paquets `.dars` ;
- `PublicationChannel` pour les réseaux de diffusion ;
- `PaymentProvider` pour les paiements futurs.

Chaque appel facturable retourne un enregistrement normalisé contenant le
client, le projet, le job, le fournisseur, le service, la quantité, l'unité, le
tarif appliqué, la devise et l'identifiant de requête du fournisseur.

Chaque client peut définir un budget mensuel indicatif, un pourcentage d'alerte
et un seuil de confirmation par traitement. Le serveur recalcule la projection
au lancement et exige une confirmation explicite quand le seuil est atteint ou
que le budget serait dépassé. Le budget avertit et confirme sans bloquer
arbitrairement un cours volontairement accepté.

Le relevé mensuel affiché, le CSV et le PDF sont trois représentations de la
même synthèse calculée depuis le registre. Le CSV neutralise les cellules qui
pourraient être interprétées comme des formules par un tableur. Le PDF est
généré dans l'API sans service externe ni coût supplémentaire.

Les factures fournisseur sont enregistrées séparément par période, fournisseur
et service. À la saisie, le serveur fige le montant interne correspondant, le
montant facturé, la tolérance et leur écart. Ces rapprochements sont immuables :
une même référence ne peut pas être enregistrée deux fois et un écart ultérieur
nécessite une nouvelle écriture explicite.

Les contributions communautaires reçues sont des écritures administratives
distinctes des paiements clients. Une allocation relie une partie d'une
contribution à un coût confirmé, à un projet et à une période, sans modifier
l'écriture de consommation initiale. Le relevé expose donc séparément le coût,
le paiement client, le financement communautaire et le solde restant.

Les contributions et allocations de la bêta sont ajoutées uniquement : elles
ne proposent ni modification ni suppression. Lors d'une allocation, le serveur
verrouille à la fois la contribution et le projet, puis refuse tout montant qui
dépasserait le solde de la contribution ou le coût confirmé encore finançable.
Cette règle reste valide lorsque plusieurs administrateurs agissent en même
temps. L'anonymat masque l'identité affichée, tandis que les informations
d'audit restent réservées à l'administration.

Le stockage est mesuré périodiquement en micro-Go-mois cumulés pour éviter les
erreurs d'arrondi sur les petits intervalles. Le tarif du fournisseur est une
configuration obligatoire en production, versionnée dans le même catalogue que
les tarifs IA. Un service de maintenance distinct mesure la dernière période
avant de supprimer les objets expirés.

## Sécurité et exploitation de la bêta

Caddy est le seul service exposé et termine HTTPS. L'API, le worker,
PostgreSQL et Redis communiquent sur des réseaux Docker privés ; le worker et
la maintenance disposent d'un réseau de sortie séparé pour joindre OpenAI ou
le stockage objet. Les processus applicatifs s'exécutent sans privilège, avec
un système de fichiers en lecture seule et des capacités Linux supprimées.

Les mots de passe et clés fournisseur sont montés comme fichiers secrets. La
clé OpenAI n'est disponible que dans le worker. Le mode `beta` refuse au
démarrage une base SQLite, un cookie non sécurisé, une origine non HTTPS, un
hôte générique ou l'exécution des traitements dans l'API.

Les requêtes de modification sont limitées aux origines autorisées, les hôtes
sont filtrés et les réponses reçoivent les en-têtes de sécurité du navigateur.
Chaque requête possède un identifiant de corrélation sans journalisation de son
corps. La connexion est limitée par compte et par adresse, avec une mémoire
bornée, et le changement de mot de passe révoque toutes les sessions.

PostgreSQL est la seule sauvegarde durable. Les dumps sont vérifiés par une
restauration dans une base éphémère ; les médias temporaires n'y sont jamais
inclus. La disponibilité, le mode dégradé Redis et les compteurs techniques
sont séparés des données fonctionnelles.

## Templates visuels des éditeurs

Chaque client dispose d'un `BrandKit` contenant son logo, ses couleurs, ses
polices autorisées et un ou plusieurs templates versionnés. La source d'un
template peut être une image PNG/JPEG ou une vidéo MP4/MOV existante. Pour une
vidéo, l'utilisateur choisit soit une image fixe extraite à un instant précis,
soit la conservation du fond animé sans sa piste audio. Un template définit le
format cible et les zones dynamiques telles que le titre du cours,
l'intervenant, la date ou le numéro d'épisode.

Les sources et leurs vignettes sont des objets permanents distincts, privés au
client et mesurés dans le même registre de stockage que les médias temporaires.
Les rendus conservent l'identifiant et la version du template afin qu'une
modification future ne change pas silencieusement une vidéo déjà produite.

Le rendu standard applique les données du projet au template sans appel à une
IA d'image. La génération IA reste facultative et ne peut remplacer que les
zones explicitement prévues à cet effet. Un nouveau rendu conserve toujours la
version du template utilisée afin de rester reproductible.

Un job de rendu reçoit un instantané des zones et de leurs valeurs, le format
cible et l'empreinte de l'analyse éditoriale. Il concatène d'abord les passages
retenus, compose la couverture, puis encode la vidéo fixe ou le fond animé sans
modifier la piste source du template. Ce parcours n'appelle aucun modèle IA.

## Archives portables

Une archive `.dars` est un ZIP dont le manifeste est versionné. Elle contient au
minimum l'analyse JSON corrigée et un audio WAV, puis éventuellement la
couverture et la vidéo produites. Le manifeste déclare le type, le chemin, la
taille et l'empreinte SHA-256 de chaque fichier ainsi que l'instantané du rendu.

La création et le réimport sont des jobs worker durables. Avant extraction, le
réimport limite le nombre et la taille totale des entrées, refuse les chemins
sortant de l'archive et vérifie chaque empreinte. L'analyse restaurée devient
directement un cours éditable : aucun appel de transcription, et donc aucun coût
IA de transcription, n'est déclenché.

## Mesure d'impact

Les workers produisent des événements d'impact idempotents indépendants de la
durée de vie des fichiers : cours terminé, vidéo rendue et archive restaurée.
Le même journal accepte déjà le futur événement `course_published` avec son
canal. Le résumé hebdomadaire privé agrège cours, durées, volume généré, stockage
cloud courant et coût issu du registre financier. La suppression d'un média
temporaire ne supprime donc pas l'historique d'impact.

## Limites de la bêta

La bêta ne comprend pas encore :

- la collecte automatique de contributions communautaires ;
- le paiement en ligne ;
- la publication automatique sur les réseaux sociaux ;
- un agent local de synchronisation ;
- l'hébergement permanent d'une médiathèque cloud.

Ces fonctions sont prévues par les contrats et le modèle de données, sans être
sur le chemin critique de la première mise à disposition.
