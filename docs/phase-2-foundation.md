# Phase 2 — Fondation multi-utilisateur

## État du premier lot

La fondation conserve le worker validé pendant la phase 1 et ajoute les limites
multi-utilisateur ainsi qu'une première interface React responsive.

### Modèle métier initial

- `User` : identité et accès ;
- `AuthSession` : session opaque révocable ;
- `Project` : espace de travail logique ;
- `Asset` : description d'une entrée temporaire ;
- `JobRecord` : métadonnées d'une opération ;
- `Artifact` : description d'un résultat temporaire ;
- `BrandKit` : identité visuelle du client.

La base ne contient aucun octet audio, image, transcription ou vidéo. Les tables
`Asset` et `Artifact` ne stockent que des métadonnées et des dates d'expiration.

### Authentification

- mots de passe Argon2 ;
- jetons de session aléatoires ;
- seul le hash SHA-256 du jeton est conservé ;
- cookie `HttpOnly`, `SameSite=Lax`, `Secure` en production ;
- aucun endpoint public de création de compte ;
- création initiale par la commande d'administration.

### Déploiement local de la fondation

```bash
cd infra
cp .env.example .env
docker compose -f compose.beta.yml up --build
```

Créer ensuite le premier utilisateur :

```bash
docker compose -f compose.beta.yml exec api \
  python backend/manage.py create-user client@example.com --name "Client pilote"
```

L'API est disponible sur `http://localhost:8000` et sa documentation sur
`http://localhost:8000/docs`.

L'interface et l'API sont livrées par le même conteneur et sur la même origine.
En développement, le serveur Vite relaie `/api` vers FastAPI :

```bash
cd frontend
npm install
npm run dev
```

Le frontend n'utilise ni CDN, ni police distante, ni script de suivi.

Le parcours web disponible permet de créer et ouvrir un projet, sélectionner un
audio local, choisir le niveau de transcription, suivre l'avancement, suspendre
ou annuler le traitement, puis télécharger l'analyse, l'audio normalisé, l'image
de couverture et la vidéo statique.

### Prochain lot

1. ~~migrations Alembic~~ — migration initiale versionnée et reprise des bases
   créées par la fondation précédente ;
2. ~~état temporaire des jobs dans Redis~~ — reprise des résultats terminés et
   marquage explicite des traitements interrompus après redémarrage ;
3. persistance des métadonnées `JobRecord` et `Artifact` ;
4. limitation globale de la concurrence ;
5. édition des segments et personnalisation de la couverture ;
6. reverse proxy HTTPS.

La migration est appliquée automatiquement au démarrage de l'API. Elle peut
aussi être lancée explicitement :

```bash
python backend/manage.py migrate
```
