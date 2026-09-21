# YuMusic — des paroles et une description de style en musique, sur Apple Silicon

**Une interface complète pour [YuE2](https://github.com/multimodal-art-projection/YuE) sur Apple Silicon, écrite pour les gens qui font de la musique plutôt que pour ceux qui écrivent du code.**

Une description de style et des paroles en entrée, un morceau en sortie.
Tous les réglages que le modèle accepte réellement — style, paroles, durée,
métrique, tempo, seed, et une harmonie que le modèle de base garde
naturellement trop prudente — tiennent sur une seule page, en clair, avec le
compromis écrit **à côté** de chaque réglage plutôt qu'enterré dans un wiki.
Rien ne sort de votre Mac.

![L'interface](docs/screenshot.png)

*Une seule page, active pendant qu'un lot tourne.*

> Interface **non officielle**. Elle n'est ni faite par l'équipe YuE / m-a-p
> ni affiliée à elle. Elle ne contient pas le modèle.

**[Installation pas à pas, sans rien supposer sur le Terminal →](INSTALL.md)**
· [This README in English →](README.md)

---

## 🤔 Pourquoi cette interface

YuE2 écrit une partition complète — mélodie et symboles d'accords, en
notation ABC — avant de rendre le moindre échantillon audio. Cette étape de
planification décide de l'harmonie, et elle démarre sur un réglage prudent.
Personne ne vous le dit non plus, et c'est la raison pour laquelle le modèle
rend systématiquement des accords parfaits bien sages, quelle que soit
l'audace de votre description de style : l'harmonie était déjà décidée,
prudemment, avant même que vos mots ne soient mis en musique.

La première chose que fait cette interface, c'est donc de mettre ce réglage
sur la page sous le nom **Harmonic daring** — un curseur qui va du défaut
prudent du modèle jusqu'à quelque chose de franchement instable — parce que
les mots « harmonie jazz » ou « chromatique » dans une description de style
ne peuvent pas atteindre une décision que le modèle prend avant même de les
lire autrement que comme du style.

Le reste de l'app suit la même règle que tout ce que je construis ainsi :
**si un réglage fait quelque chose, le dire, dans la phrase juste à côté.**

---

## 🎛️ Ce qu'elle fait

**Fabriquer un morceau à partir d'une description de style et de paroles.**
Paroles en anglais, balises de section (`[Verse]`, `[Chorus]` et cinq
autres) insérées au curseur d'un clic, et un interrupteur **Instrumental**
pour aucune voix du tout.

**Harmonic daring**, du défaut prudent du modèle jusqu'à quelque chose qui
vous surprendra vraiment — voir plus haut.

**Retrouver un morceau entièrement.** Chaque rendu écrit un `.txt` à côté de
lui avec tous les réglages utilisés, et la partition elle-même en `.abc`.
Déposez **n'importe quel fichier appartenant à un morceau** — le `.txt`, le
`.wav`, la partition — et tous les réglages de la page reviennent dans
l'état qui l'a produit. Le seed est ce qui reproduit la composition :
laissez-le tel quel et augmentez la durée, vous obtenez la même pièce, plus
longue.

**Des lots qui restent modifiables.** Jusqu'à 100 morceaux, et **tout sur la
page reste actif pendant qu'un lot tourne** — changez le style, les
paroles, la durée, la métrique, l'audace, même la variante du modèle,
pendant le morceau 3, et **le morceau 4 obéit**. Deux choses se figent une
fois Generate pressé, parce qu'elles décident de la forme du lot plutôt que
d'un morceau : le **nombre de morceaux** et le **nom du lot**.

**Prompts dynamiques et séquentiels**, dans le style comme dans les
paroles :

🎲 **Dynamique.** Une option tirée par rendu, fraîche à chaque fois :

```
{slow|fast} {piano|guitar} piece, {warm|cold}
```

🔁 **Séquentiel.** Des versions complètes séparées par une ligne de trois
tirets ou plus, utilisées une par morceau, dans l'ordre, en boucle si vous
dépassez la fin :

```
première idée
---
deuxième idée
```

**Un modèle local peut rédiger une description de style** à partir d'une
idée informelle — *a sad the cure track, slow, with choir at the end* — via
[Ollama](https://ollama.com), qui tourne entièrement sur votre Mac. Son
vrai rôle n'est pas de polir mais de **traduire une référence en faits
musicaux**, ce qu'une bibliothèque de presets ne peut jamais faire : YuE2 ne
sait pas qui est The Cure, mais il sait ce que veut dire *post-punk,
baryton masculin mélancolique, guitare aux échos, production noyée de
réverbe*. Facultatif ; le reste de l'app fonctionne sans.

**La partition de chaque morceau**, le score ABC que YuE2 a planifié avant
de rendre le moindre échantillon, consultable directement sur la page.

**Un dossier par lot**, nommé par vous, contenant l'audio, la partition, le
`.txt` des réglages, et en option des copies MP3, FLAC et MIDI à côté du
WAV.

**Deux boutons pour « où c'est parti ? »** — le dossier dans lequel ce lot
écrit, depuis la barre de lancement, et le morceau en cours d'écoute,
sélectionné dans le Finder.

---

## ⚠️ Deux choses à savoir avant le premier rendu

**1. Le plafond de durée du modèle lui-même est de six minutes.** Demander
plus de 360 secondes s'arrête simplement à six minutes.

**2. Instrumental jette les paroles plutôt que de les faire taire.** Cochez
la case et ce qu'il y a dans la case Paroles est ignoré au profit d'un
marqueur instrumental explicite — pas besoin de vider la case d'abord, et
laisser du texte dedans ne coûte rien.

---

## 🍏 Ce qu'il faut

| | |
|---|---|
| **Mac** | Apple Silicon — M1 ou plus récent. Les Mac Intel ne peuvent pas. |
| **Mémoire** | Non mesuré en dessous de 64 Go ici ; les poids du modèle sont petits (4,2 Go en 8-bit), donc 16 Go devraient suffire. |
| **Disque** | ~5 Go pour le modèle 8-bit recommandé ; jusqu'à ~11 Go de plus si vous ajoutez aussi bf16 et 4-bit. |
| **macOS** | Sonoma (14) ou plus récent. |
| **En plus** | Le modèle YuE2-3B-MLX — voir [INSTALL.md](INSTALL.md). |
| **Optionnel** | [Ollama](https://ollama.com) (rédaction de description de style) ; ffmpeg n'est **pas** nécessaire — l'export MP3/FLAC est intégré. |

---

## ⏱️ Mesuré par les auteurs du modèle, sur un Mac de série M

Pas encore de chronométrage maison ici — ces chiffres viennent de la fiche du
modèle [YuE2-3B-MLX](https://huggingface.co/ahmadw/YuE2-3B-MLX) elle-même,
cités et non estimés :

| Variante | Vitesse de décodage | Taille |
|---|---|---|
| 8-bit (recommandée) | ~80 tokens/s avec CFG | 4,2 Go |
| bf16 (qualité de référence) | ~70 tokens/s (~35 avec CFG) | 7,0 Go |
| 4-bit (la plus rapide, un peu de perte) | la plus rapide, qualité en retrait | 3,4 Go |

Un morceau de trois minutes prend quelques minutes de bout en bout sur
Apple Silicon.

---

## 🎚️ Les réglages, dans l'ordre

### Le modèle

**Model variant.** Le 8-bit est celui à utiliser — qualité proche du bf16,
environ deux fois plus rapide à décoder. Le bf16 est la référence et le
plus lent. Le 4-bit est le plus rapide et s'écarte le plus de la référence.
**Seules les variantes réellement téléchargées apparaissent** — le menu
s'adapte à ce qui est sur le disque.

### Paroles (Words)

**Style prompt.** Une liste de faits musicaux concrets séparés par des
virgules, pas des phrases ni un tas d'adjectifs — grosso modo : genre,
époque ou esthétique, caractère vocal, instruments, caractère rythmique,
langage harmonique, production, tempo approximatif, ambiance. *« Beautiful,
emotional, amazing » ne dit rien que le modèle puisse jouer ; « restrained
female alto, dry close vocal » lui dit exactement quoi faire.*

**Lyrics**, avec des balises de section insérées au curseur par sept
boutons au-dessus de la case, pour coller les paroles d'abord et les
baliser ensuite.

**Instrumental** — voir l'avertissement plus haut.

### Réglages (Settings)

**Target duration**, en secondes, plafonnée à 360 par le modèle lui-même.

**Track seed**, `-1` pour un seed aléatoire à chaque morceau, ou un nombre
fixe pour reproduire une pièce — chaque morceau écrit son propre seed dans
son `.txt`.

**Walk the seed** — avec un seed fixe, ajoute 1 par morceau supplémentaire :
même famille, vraie variation. Ignoré tant que le seed est `-1`, déjà
aléatoire.

**Metre and tempo** — un menu de métrique et un nombre de tempo, `0` pour
laisser le modèle décider.

**Rhythmic complexity**, un curseur du simple au complexe.

**Harmonic daring** — voir *Pourquoi cette interface* plus haut.

**Style strength**, à quel point le rendu est poussé vers la formulation
exacte du style et des paroles.

### Avancé (Advanced)

**Score planning** — *melody + chords* est ce sur quoi agit Harmonic
daring ; le désactiver va directement au son, et il n'y a alors plus de
partition à consulter. **Start from an existing score** donne à l'app un
fichier `.abc` à rendre tel quel, ce qui permet de faire revenir dans le
modèle une partition modifiée à la main. **Audio refinement steps** —
laisser vide pour le défaut du modèle.

### Le lot (The run)

**Number of tracks**, jusqu'à 100, chacun avec son propre seed, tous dans
un même dossier nommé d'après le **batch name**. **Extra formats** — copies
MP3, FLAC et MIDI à côté du WAV et de la partition `.abc`, toujours écrits.

### Résultats (Results)

Le lecteur audio, un bouton pour révéler le morceau en cours dans le
Finder, le prompt résolu pour le morceau en cours, la liste des fichiers
enregistrés par ce lot, un journal, et la partition du dernier morceau.

---

## 🔧 Notes de conception

Quelques décisions volontaires, au cas où elles ressembleraient à des
oublis :

- **L'app appelle le pipeline du dépôt du modèle plutôt que de le
  réimplémenter.** La seule chose qu'elle ajoute, c'est d'exposer les
  réglages d'échantillonnage de l'étape de planification — température,
  top-p, top-k — que la ligne de commande du dépôt du modèle n'expose pas.
  C'est exactement ce que tourne « Harmonic daring ».
- **Le repetition penalty n'est volontairement pas exposé.** La notation
  ABC doit répéter constamment des barres de mesure, des silences et des
  lettres de notes ; le pénaliser corromprait la notation plutôt que
  d'assouplir l'harmonie.
- **Rien n'est jamais envoyé nulle part.** Pas de télémétrie, pas de
  compte, aucun appel réseau sauf celui qui télécharge les poids du modèle,
  une seule fois.

---

## 📜 Licences et attribution

**Cette interface** est en MIT — voir [LICENSE](LICENSE). Faites-en ce que
vous voulez.

**Le modèle n'est pas inclus ici, et sa licence n'est pas MIT.** Les poids
YuE2-3B-MLX que cette app pilote sont téléchargés séparément par vous, et
ils sont sous licence **CC BY-NC 4.0 — non commerciale** par leurs auteurs
d'origine, héritée de
[m-a-p/YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B) et
[m-a-p/YuE2-Vae](https://huggingface.co/m-a-p/YuE2-Vae). C'est une vraie
restriction, pas une formalité : vérifiez
[la fiche du modèle](https://huggingface.co/ahmadw/YuE2-3B-MLX) et le
[projet YuE2 d'origine](https://github.com/multimodal-art-projection/YuE)
avant d'utiliser commercialement quoi que ce soit que vous en tirez, plutôt
que de me croire sur parole — les licences changent, et celle-ci est déjà
plus stricte que la plupart.

Rien dans ce dépôt n'est de l'audio généré, et aucun son que vous faites
avec ne passe par moi ni par personne.

---

## 👋 Qui a fait ça

Jean-Pascal — **[Quick-Eyed Sky](https://www.youtube.com/@QuickEyedSky)**
sur YouTube, [QES](https://huggingface.co/QES) sur Hugging Face. Pas
programmeur : ceci existe parce que l'harmonie était trop sage et que je
voulais ouvrir le seul réglage qui la contrôle vraiment.

Si ça vous a épargné un après-midi, vous pouvez
[m'offrir un café](https://buymeacoffee.com/oFJ5CiY7n). Entièrement
facultatif, et le projet reste exactement aussi gratuit dans les deux cas.

---

## 🙏 Merci

À [l'équipe YuE2 / m-a-p](https://github.com/multimodal-art-projection/YuE)
pour le modèle, et à
[ahmadw](https://huggingface.co/ahmadw/YuE2-3B-MLX) pour le portage MLX
natif qui le fait tourner ainsi sur un Mac.
