# Revisiter · Assistant d’analyse de code

Chatbot en français pour comprendre une application, retrouver ses fonctions et ses
relations, examiner des améliorations et préparer une transformation guidée.
Projet académique en binôme, rendu le **16 novembre 2026**.

## Fonctionnalités

- Interface Flask avec discussion progressive, historique, fichiers joints et passages de code cliquables.
- Import de fichiers, dossiers, archives ZIP et dépôts GitHub publics.
- Recherche hybride, avec encodeurs CamemBERT ou BERT et génération locale par Ollama.
- Analyse syntaxique multilangage, appelants directs/indirects et comparaison de versions.
- Propositions de code, différences, tests proposés et étapes de transformation à examiner.
- Espaces de travail persistants, comptes optionnels, dictée vocale locale avec confirmation du texte.
- Masquage des secrets détectés, annulation des générations et diagnostic des temps de réponse.
- Évaluation des réponses sur des cas attendus et revue humaine des résultats.

## Installation sous Windows

Dans le dossier du projet, ouvrez PowerShell avec Python 3.11 installé :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,nlp]"
Copy-Item .env.example .env
```

Installer et démarrer Ollama, puis télécharger le modèle local :

```powershell
ollama pull qwen2.5-coder:3b
```

Le fichier `.env.example` utilise Ollama local : aucune clé ni crédit API n’est
nécessaire. Ne jamais publier le fichier `.env` contenant une configuration réelle.
Le premier usage d’un encodeur télécharge ses poids ; les questions suivantes réutilisent le cache.

Pour activer la dictée vocale (optionnelle) :

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-vocales.txt
```

Le modèle de transcription est téléchargé au premier usage, puis fonctionne localement.
La transcription peut être corrigée avant l’envoi de la question.

## Lancer le chat Flask

```powershell
.\.venv\Scripts\python.exe -m flask --app revisiter.web run --host 127.0.0.1 --port 8540
```

Ouvrir http://127.0.0.1:8540/ et laisser le terminal ouvert. Le bouton
**Essayer avec Atlas** charge un exemple. Les conversations et leurs fichiers
sont enregistrés dans le stockage local de Revisiter.

La cartographie avancée, le plan guidé avec suivi et l’évaluation restent
accessibles dans l’atelier Streamlit. Depuis le même dossier, les deux
interfaces retrouvent les espaces enregistrés dans le même stockage.

    .\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1

## Tester

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

La livraison de référence a passé **367 tests** sous Windows. Ces tests vérifient
les comportements techniques ; ils ne prouvent pas l’exactitude de toutes les réponses d’un modèle.

Pour un premier essai, importer `examples/demo.py` puis demander :

- « Explique la fonction total_panier et cite les lignes utilisées. »
- « Quel est le résultat de total_panier([10, -3]) ? »
- « Quels contrôles des entrées seraient utiles ? Justifie avec le code. »

## Validation isolée des tests proposés

L’exécution du code importé exige Docker local en mode conteneurs Linux et une image
préparée explicitement :

```powershell
.\.venv\Scripts\python.exe -m revisiter.validation_isolee --preparer
```

Sans Docker, le résultat reste **non exécuté**, sans repli sur le Python du poste.
L’intégration vise Python/pytest et JavaScript/node:test. Elle n’a pas encore été
vérifiée avec un moteur Docker réel sur le poste de référence.

## Limites et données

Les citations et certaines affirmations calculables sont contrôlées par des règles
bornées. L’analyse syntaxique ne résout pas tous les appels dynamiques. Les réponses
du modèle, recommandations et transformations demandent une revue humaine.
La latence dépend du matériel, du contexte et du chargement des modèles.

Les données de travail locales sont conservées sous `.revisiter/`, exclu de Git.
Le masquage par motifs peut manquer des secrets : importer uniquement du code dont
le partage et l’analyse sont autorisés. Le dépôt n’inclut ni modèles, ni clé API,
ni historique de discussion, ni enregistrement audio.

Les documents `docs/planning.md` et `docs/backlog.md` conservent le cadrage initial.
Voir `docs/architecture.md` pour l’organisation actuelle et `docs/verification.md`
pour le périmètre de validation.
