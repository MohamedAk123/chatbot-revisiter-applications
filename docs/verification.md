# Vérification du socle

Vérification locale le 31 octobre 2026 avec Python 3.11.9 sous Windows.

- `pytest -q` : 6 tests réussis.
- `ruff check .` : aucun défaut signalé.
- Aucun appel au fournisseur IA ; aucune clé utilisée.
- Les poids CamemBERT/BERT ne sont pas téléchargés pendant ces tests.
- Les appels réels, la qualité NLP et les objectifs du CDC restent à mesurer.

## Versions utilisées

- langchain : 1.4.7
- langchain-core : 1.7.3
- langgraph : 1.2.19
- langchain-ollama : 1.1.0
- ollama : 0.6.3
- python-dotenv : 1.2.4
- pytest : 9.2.0
- ruff : 0.17.3
