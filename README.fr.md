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

## 🆕 Nouveau dans la 2.22

- **La durée, expliquée et maîtrisée.** YuE2 écrit toujours un morceau
  *entier* d'abord — en général 2 à 3 minutes de partition — et la durée
  décide seulement où le son s'arrête. Mesuré sur 181 morceaux, le rendu
  médian jouait **45 %** de sa propre partition ; à 30 secondes, on entend
  l'intro. Après chaque morceau, la page dit maintenant combien durait la
  partition et quelle part vous en avez entendue, et un nouveau choix décide
  de ce qui se passe quand la partition est plus longue : couper le son
  (comme avant), **raccourcir la partition** pour qu'elle finisse à une fin de
  section, ou **jouer toute la partition**.
- **Harmonic daring dit à quoi s'attendre** à chaque réglage — combien
  d'accords, à quelle fréquence la tonalité change, à quelle fréquence la
  partition se casse, mesuré sur 181 morceaux — au lieu d'afficher des
  chiffres d'échantillonnage.
- **Style strength devient « Style-prompt fidelity », dans Advanced.** Il
  double le temps de rendu et son effet n'a pas encore été mesuré.
- **Une page plus claire :** toutes les explications réécrites et plus
  courtes, Words sur deux colonnes, une barre de lancement plus nette, et un
  lecteur qui est un lecteur, pas une zone de dépôt.

Les anciennes versions restent disponibles — voir [Versions](#-versions).

---

## 🧪 Encore expérimental

Trois réglages de cette page ne tiennent pas entièrement ce que leur nom
promet, et l'enterrer dans un tableau plus bas serait malhonnête.

**Harmonic daring** pousse l'échantillonnage de l'étape de planification du
modèle au-delà de la plage pour laquelle il a été réglé. C'est tout le
mécanisme - il n'y a pas de version « sûre » cachée en dessous d'une
partition audacieuse. Des réglages élevés peuvent produire, et produisent
parfois, une partition qui se délite plutôt qu'une partition simplement
surprenante. La ligne sous le curseur dit ce que chaque réglage a donné sur
181 morceaux de test : 7 à 9 y était la zone utile, et 10 cassait parfois la
partition. Des prompts différents, peu de morceaux pour certains réglages :
une tendance, pas une promesse.

**Instrumental ne fait pas taire le modèle de façon fiable.** Cocher la
case demande à YuE2 de n'avoir aucune voix du tout, et cela réduit
nettement le chant - mais le modèle de base n'obéit pas à cette consigne
comme le ferait un interrupteur dédié. Certains rendus reviennent quand
même avec du chant. Considérez un rendu réellement instrumental comme une
bonne surprise de ce rendu-là, pas comme une garantie que donne ce réglage.

**Shorten the score to fit** (nouveau dans la 2.22) n'a pas encore été jugé
à l'oreille sur beaucoup de morceaux : on ne sait pas si YuE2 joue une vraie
fin quand la partition s'arrête avant sa propre outro. Écoutez les dernières
secondes de quelques morceaux avant de lui confier un long lot.

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
restaurez un morceau, choisissez **Play the whole score**, et vous obtenez la
même pièce, sans coupure.

**Savoir ce que vous avez entendu.** Après chaque morceau : *Score: 53 bars,
about 2:20. Heard: 1:00 = bars 1 to 22, 43% of the score. Never reached:
chorus, interlude, outro.* La même ligne est écrite dans le `.txt` du
morceau.

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

## ⚠️ Trois choses à savoir avant le premier rendu

**1. La durée ne raccourcit pas la musique.** Le modèle écrit d'abord un
morceau entier, et la durée dit seulement où le son s'arrête. Avec des
paroles, la longueur suit les paroles (moins de lignes, morceau plus court).
En instrumental, seul *Shorten the score to fit* raccourcit le morceau
lui-même.

**2. Le plafond de durée du modèle lui-même est de six minutes.** Demander
plus de 360 secondes s'arrête simplement à six minutes.

**3. Instrumental jette les paroles plutôt que de les faire taire.** Cochez
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
| **Optionnel** | [Ollama](https://ollama.com) (rédaction de description de style). **Pour l'export MP3/FLAC/MIDI et l'aperçu de la partition :** `ffmpeg`, `abcmidi` et `abcm2ps`, tous via Homebrew — voir [INSTALL.md](INSTALL.md). Sans eux, cocher ces cases écrit une ligne dans le Log expliquant ce qui manque, plutôt qu'un succès silencieux qui n'en est pas un. |

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
boutons à côté de la case, pour coller les paroles d'abord et les baliser
ensuite.

**Instrumental** — voir l'avertissement plus haut.

### Réglages (Settings)

**Length** — la durée visée en secondes (360 au plus), et que faire quand la
partition est plus longue : *couper le son à la durée* (comme avant),
*raccourcir la partition* (elle finit à la fin de section la plus proche ;
une autre prise), ou *jouer toute la partition* (la même prise que *couper*,
en plus long ; la durée est ignorée).

**Seed** — `-1` pour un seed aléatoire à chaque morceau, ou un nombre fixe
pour reproduire une pièce — chaque morceau écrit son propre seed dans son
`.txt`. Avec un seed fixe, *ajouter 1 par morceau supplémentaire* donne des
pièces voisines plutôt que des copies.

**Metre and tempo** — un menu de métrique et un nombre de tempo, `0` pour
laisser le modèle décider.

**Rhythmic complexity**, un curseur du simple au complexe. La page affiche
la phrase exacte qu'il ajoute à votre style.

**Harmonic daring** — voir *Pourquoi cette interface* plus haut. La ligne
sous le curseur dit ce que chaque réglage a donné sur 181 morceaux de test.

### Avancé (Advanced)

**Style-prompt fidelity (CFG)** — à quel point le son est poussé vers la
formulation exacte du style et des paroles. 1.0 = désactivé ; au-dessus, un
rendu prend environ deux fois plus de temps. Pas encore mesuré : comparez
quelques morceaux avant de l'utiliser sur un lot. **Score planning** — *melody + chords* est ce sur quoi agit Harmonic
daring ; le désactiver va directement au son, et il n'y a alors plus de
partition à consulter. **Start from an existing score** donne à l'app un
fichier `.abc` à rendre tel quel, ce qui permet de faire revenir dans le
modèle une partition modifiée à la main. **Audio refinement steps** — 32
est le réglage du modèle ; le laisser là, sauf pour expérimenter.

### Le lot (The run)

**Number of tracks**, jusqu'à 100, chacun avec son propre seed, tous dans
un même dossier nommé d'après le **batch name**. **Extra formats** — copies
MP3, FLAC et MIDI à côté du WAV et de la partition `.abc`, toujours écrits.

### Résultats (Results)

Le lecteur audio, la durée de la partition et la part entendue, un bouton
pour révéler le morceau en cours dans le Finder, le prompt résolu pour le morceau en cours, la liste des fichiers
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
- **La longueur est mesurée, pas devinée.** `score_length.py` lit la
  partition en mesures et en secondes, et le renderer la coupe à une fin de
  section *avant* que l'audio soit fabriqué, dans le processus même du
  modèle — sans second chargement du modèle.
- **Rien n'est jamais envoyé nulle part.** Pas de télémétrie, pas de
  compte, aucun appel réseau sauf celui qui télécharge les poids du modèle,
  une seule fois.

---

## 📦 Versions

Cette page décrit la **2.22**, la version actuelle. Les versions
précédentes restent disponibles : chacune est un
[tag](https://github.com/Quick-Eyed-Sky/yumusic-mac-mlx/tags) avec son propre
ZIP à télécharger, et avec git, `git checkout v2.20` ramène la première version
publique.

| Version | |
|---|---|
| **2.22** (actuelle) | Durée expliquée et maîtrisée, daring qui dit à quoi s'attendre, Style-prompt fidelity dans Advanced, page réécrite. |
| 2.20 | Première version publique. |

**Mettre à jour :** avec git, `git pull` dans le dossier. Avec un ZIP, vos
rendus sont dans le dossier `outputs` de l'app — sortez ce dossier avant de
remplacer l'ancien dossier par le nouveau.

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
