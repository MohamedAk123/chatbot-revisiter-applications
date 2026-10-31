# Chatbot Revisiter les applications

Prototype académique en binôme, à rendre le **16 novembre 2026**. L'assistant doit
aider un développeur à comprendre une application, identifier des améliorations
justifiées et appliquer lui-même une transformation guidée.

## État de cette première version

Ce dépôt contient le **socle technique**, pas encore le MVP complet :

- Configuration Ollama Cloud avec une clé locale, ou Ollama local sans clé.
- Import explicite de fichiers UTF-8, contrôle du format, du volume et des contenus binaires.
- Découpage avec conservation des chemins et numéros de lignes.
- Adaptateurs expérimentaux CamemBERT et BERT multilingue.
- Premier graphe LangGraph : recherche de passages puis question/réponse via LangChain.
- Tests sans appels IA et vérifications GitHub Actions.

L'interface, les sessions persistantes, le résumé global, les recommandations structurées,
la transformation guidée, l'export et les retours utilisateurs restent à développer.
Les citations sont demandées au modèle ; leur validité sémantique n'est pas encore contrôlée.
Les passages fournis au modèle sont affichés séparément des citations de sa réponse.

## Rôle des technologies

| Technologie | Rôle retenu |
|---|---|
| Python 3.11 à 3.13 | Traitement, services et tests |
| LangChain | Messages, interface d'embeddings et connexion au modèle |
| LangGraph | Organisation du traitement ; puis état des étapes guidées |
| Ollama | Accès au modèle génératif local ou Cloud |
| CamemBERT | Base expérimentale pour les textes et questions en français |
| BERT multilingue | Variante de comparaison pour la recherche de passages |

CamemBERT/BERT sont ici des encodeurs, et non le moteur de génération des réponses.
Les modèles de base avec moyenne des tokens ne sont pas spécialisés dans la recherche
sémantique de code. Nous mesurerons leur intérêt sur les mêmes questions ; aucune
supériorité de l'un des modèles n'est supposée. Le modèle génératif précis reste à choisir.

## Installation Windows

Dans le dossier du dépôt, avec Python 3.11 installé :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,nlp]"
Copy-Item .env.example .env
```

Ouvrir `.env` dans son éditeur et renseigner `OLLAMA_MODEL` et `OLLAMA_API_KEY`.
Le mode Cloud utilise `https://ollama.com` avec le chemin `/api/chat` géré par le client.
La clé se crée sur [Ollama](https://ollama.com/settings/keys).
Choisir un nom de modèle disponible dans [la liste API](https://ollama.com/api/tags).
Ne jamais partager `.env` ni envoyer la clé dans une conversation ou un commit.

Le premier usage de CamemBERT/BERT télécharge ses poids depuis Hugging Face et peut
prendre du temps. Ils s'exécutent ensuite localement. Une connexion Cloud transmet
les extraits sélectionnés et la question au fournisseur Ollama. N'utiliser que des fichiers
autorisés et sans secrets. L'appel IA est déclenché explicitement par la commande ci-dessous.

```powershell
.\.venv\Scripts\revisiter.exe examples/demo.py --question "Comment est calculé le total du panier ?"
```

Pour une instance locale : `OLLAMA_MODE=local`, `OLLAMA_API_KEY=` et un
`OLLAMA_MODEL` préalablement installé sur l'instance `localhost:11434`.

## Vérifications sans clé et sans téléchargement de modèles

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

Les tests utilisent des doubles pour le modèle et les embeddings. Ils ne démontrent
pas la qualité de CamemBERT/BERT ni celle des réponses réelles du fournisseur.

## Organisation du travail

- [Architecture et décisions](docs/architecture.md)
- [Planning jusqu'au rendu et répartition proposée](docs/planning.md)
- [Fonctionnalités et critères d'acceptation](docs/backlog.md)

Créer une branche par fonctionnalité et une pull request relue par l'autre membre.
La répartition A/B du planning est une proposition ; les noms sont à compléter.
Le dépôt est privé à sa création. L'ajout du binôme attend son identifiant GitHub.

## Sources techniques

- [Intégration LangChain et Ollama](https://docs.langchain.com/oss/python/integrations/chat/ollama)
- [Ollama Cloud et clé API](https://docs.ollama.com/cloud)
- [Interruptions LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [CamemBERT](https://huggingface.co/almanach/camembert-base)
- [BERT multilingue](https://huggingface.co/google-bert/bert-base-multilingual-cased)
