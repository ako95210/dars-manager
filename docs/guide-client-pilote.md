# Guide du client pilote

## Première connexion

1. Ouvrez l'adresse HTTPS transmise par l'administrateur.
2. Ouvrez l’invitation reçue par e-mail et choisissez votre mot de passe. Le
   compte ne peut pas être utilisé avant cette vérification.
3. Connectez-vous avec votre adresse e-mail et ce mot de passe. En cas d’oubli,
   utilisez le lien « Mot de passe oublié ? » sur la page de connexion.

Dars Manager fonctionne dans le navigateur sous Windows, macOS et Linux. Il
n'est pas nécessaire d'installer l'application ni un moteur de traitement.

## Produire un premier cours

1. Créez un projet depuis la vue d'ensemble.
2. Ouvrez-le et choisissez le fichier audio.
3. Vérifiez l'estimation de coût ; confirmez-la si un seuil est atteint.
4. Lancez le traitement. Vous pouvez fermer la page : le worker cloud continue.
5. Retrouvez l'avancement dans **Traitements** après votre reconnexion.
6. Choisissez la transcription et, si vous le souhaitez, le chapitrage. Sans
   chapitrage, donnez un titre au cours entier.
7. Dans **Création Audio**, corrigez les titres et sélectionnez vos extraits.
   Dans **Sous-titres**, corrigez ou traduisez le texte si nécessaire ; la
   correction IA affiche un coût estimé avant lancement.
8. Dans **Création Vidéo**, choisissez le visuel et l’option d’incrustation des
   sous-titres. Téléchargez ensuite les résultats ou l’archive `.dars`.

## Conservation et confidentialité

Une archive `.dars` temporaire est créée automatiquement après les étapes
terminées et les corrections enregistrées. Lors d’un retour sur le cours,
Dars Manager propose de télécharger la dernière archive disponible. Ce n’est
pas une sauvegarde durable : elle expire par défaut après sept jours.

Les médias de travail sont temporaires et leur date d'expiration est affichée.
Téléchargez les fichiers à conserver avant cette date. Les comptes, projets,
états de traitement, coûts et mesures d'impact restent enregistrés afin de
restaurer le dashboard et produire des relevés auditables.

Un utilisateur ne peut consulter que ses projets, médias, traitements,
templates et relevés. La clé du fournisseur IA n'est jamais envoyée au
navigateur.

## Coûts

La rubrique **Coûts** présente les montants confirmés, estimés, payés et
éventuellement financés par la communauté. Les relevés CSV et PDF sont
téléchargeables par mois. Aucun paiement en ligne n'est réalisé dans la bêta ;
les règlements sont enregistrés manuellement par l'administrateur.

## Signaler un problème

Indiquez à l'administrateur :

- l'heure approximative ;
- le projet et l'action effectuée ;
- le message affiché ;
- la valeur `X-Request-ID` si elle est visible dans les outils réseau du
  navigateur ;
- aucun mot de passe, aucune clé API et aucun contenu confidentiel inutile.
