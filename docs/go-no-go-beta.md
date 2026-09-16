# Décision go/no-go de la bêta

Date de revue : 14 septembre 2026.

## Décision actuelle

**GO technique pour le déploiement de recette. NO-GO pour inviter le client
tant que les contrôles externes ci-dessous ne sont pas cochés sur le serveur
réel.**

Le code fournit le chemin HTTPS, l'isolation des secrets, les contrôles
d'autorisation, les sauvegardes vérifiables, les healthchecks, les journaux et
le guide d'accueil. Le domaine, le serveur, le certificat public et la copie de
sauvegarde hors hôte dépendent de l'environnement de déploiement et ne peuvent
pas être attestés depuis le poste de développement.

## Contrôles validés dans le dépôt

- [x] sessions en cookie HttpOnly/Secure/SameSite ;
- [x] contrôle d'origine sur toutes les méthodes de modification ;
- [x] hôtes autorisés explicites et configuration bêta fail-closed ;
- [x] mots de passe Argon2 et changement avec révocation des sessions ;
- [x] limitation des échecs de connexion ;
- [x] comptes inactifs avant vérification, invitations hachées et expirantes ;
- [x] ressources métier filtrées par propriétaire et administration séparée ;
- [x] secrets Docker montés par fichier, clé OpenAI absente de l'API ;
- [x] API et worker non-root, systèmes de fichiers en lecture seule ;
- [x] PostgreSQL et Redis non publiés sur Internet ;
- [x] reverse proxy Caddy et renouvellement TLS automatique ;
- [x] healthchecks, logs JSON, request IDs et métriques administrateur ;
- [x] scripts de sauvegarde et de restauration de contrôle ;
- [x] tests automatisés d'expiration, reprise worker et non-double-facturation ;
- [x] parcours d'accueil du pilote documenté.

## Contrôles obligatoires sur le serveur réel

- [ ] DNS du domaine dirigé vers le serveur ;
- [ ] certificat HTTPS public valide et renouvellement vérifié ;
- [ ] secrets générés, permissions contrôlées et clé OpenAI plafonnée ;
- [ ] pare-feu limité à SSH administrateur et TCP 80/443 ;
- [ ] sauvegarde PostgreSQL créée puis restaurée avec succès ;
- [ ] copie de sauvegarde chiffrée hors hôte ;
- [ ] alerte de disponibilité reçue sur le canal choisi ;
- [ ] sonde de charge sans erreur et p95 inférieur à 500 ms ;
- [ ] cours de référence traité, téléchargé et réimporté ;
- [ ] validation du coût réel et de l'expiration par l'administrateur ;
- [ ] domaine d'envoi validé, SPF/DKIM/DMARC publiés et livraison SMTP testée ;
- [ ] compte pilote créé et activé depuis l'invitation reçue.

Le passage à **GO client** exige la totalité de cette seconde liste. Toute
exception doit être datée, justifiée et acceptée explicitement avant
l'ouverture.

## Résultats de l'exercice local du 14 septembre 2026

- image applicative construite et exécutée avec `uid=10001(dars)` ;
- migrations `0001` à `0012` appliquées sur PostgreSQL 17 ;
- dump custom produit puis restauré dans une base éphémère : révision
  `20260915_0012` contrôlée ;
- arrêt de Redis : API disponible en `degraded`, PostgreSQL toujours prêt ;
- sonde interne : 500 requêtes, concurrence 25, aucune erreur, 328,9 req/s,
  latence p95 117,69 ms et p99 134,09 ms ;
- configuration Caddy validée avec Caddy 2.11.4 ;
- suite automatisée : 51 tests backend réussis et build frontend réussi.

Ces chiffres valident la machine de développement et ne remplacent pas la
mesure depuis Internet vers le futur serveur bêta.
