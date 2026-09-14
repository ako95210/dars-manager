# Matrice d'autorisations de la bêta

Les ressources privées répondent `404` lorsqu'elles appartiennent à un autre
client. Cela évite de confirmer l'existence d'un identifiant valide. Les routes
administratives répondent `403` à un client authentifié.

| Ressource | Lecture/écriture client | Administration | Contrôle appliqué |
| --- | --- | --- | --- |
| Projets | propriétaire uniquement | pas d'accès transversal par défaut | `Project.user_id` |
| Uploads et assets | propriétaire uniquement | pas d'accès transversal par défaut | `Asset.user_id` et projet propriétaire |
| Jobs et progression | propriétaire uniquement | pas d'accès transversal par défaut | recherche `JobManager` par `user_id` |
| Artefacts et téléchargements | propriétaire uniquement | pas d'accès transversal par défaut | job possédé puis `Artifact.user_id` |
| Analyse et exports | propriétaire du job source | pas d'accès transversal par défaut | job, projet, artefact et template possédés |
| Templates et vignettes | propriétaire uniquement | pas d'accès transversal par défaut | `BrandTemplate.user_id` et fichier possédé |
| Archives `.dars` | propriétaire de l'asset et du projet | pas d'accès transversal par défaut | `Asset.user_id` et projet propriétaire |
| Coûts, impact et relevés | utilisateur courant uniquement | synthèses séparées par client | dépendance `require_user`, filtre `user_id` |
| Paiements et factures fournisseur | aucune écriture client | administrateur uniquement | dépendance `require_admin` |
| Contributions et allocations | financement visible sans identité privée | administrateur uniquement | dépendance `require_admin`, réponse client réduite |
| État système et métriques | aucun accès | administrateur uniquement | dépendance `require_admin` |
| Liveness/readiness | public, données minimales | public | aucun secret ni donnée client |

Les tests d'API exercent les tentatives croisées sur les projets, uploads,
templates, vignettes, jobs, analyses, exports et routes financières. Toute
nouvelle route qui reçoit un identifiant métier doit ajouter un test équivalent
avant intégration.
