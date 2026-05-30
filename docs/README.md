# PEPSS Golf — Page d'inscription

Page web autonome pour gérer les inscriptions d'une équipe de golf (8 joueurs)
aux différents matchs. Pour chaque date, un joueur peut s'inscrire en :

- **Simple**
- **Double**
- **Réserve**

## Utilisation

Ouvrez simplement `index.html` dans un navigateur (double-clic), ou publiez-le
en ligne via GitHub Pages.

### Publication via GitHub Pages

Ce dossier `docs/` est prévu pour être servi par GitHub Pages :
**Settings → Pages → Build and deployment → Source : « Deploy from a branch »**,
puis choisir la branche voulue et le dossier **`/docs`**.

L'URL publique sera alors : `https://<utilisateur>.github.io/Nikobus-HA/`

### Fonctions

- Gestion de l'équipe : ajout/suppression/renommage des joueurs.
- Ajout de dates de match (avec adversaire/lieu optionnel).
- Inscription par joueur et par date (Simple / Double / Réserve).
- Compteurs par match (Simple, Double, Réserve, Sans réponse).
- Export / Import des inscriptions au format JSON pour partage ou sauvegarde.

## Stockage des données

Les inscriptions sont enregistrées dans le navigateur via `localStorage`,
donc **propres à chaque appareil/navigateur**. Pour partager l'état entre
plusieurs personnes, utilisez le bouton **Exporter (JSON)** puis
**Importer (JSON)**.

> Besoin d'inscriptions partagées en temps réel (tous les joueurs voient la
> même chose) ? Il faut un petit backend (par ex. Google Sheets, Firebase ou
> une API). Dites-le-moi et je peux faire évoluer la page dans ce sens.
