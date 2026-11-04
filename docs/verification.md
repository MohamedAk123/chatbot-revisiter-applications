# Vérification

La livraison du 3 novembre 2026 a passé **367 tests** sous Windows avec Python 3.11.
Les tests isolent les comptes et les données. Ils couvrent notamment l’import,
les références, les routes statiques, la persistance, les propositions, la dictée,
l’annulation, la comparaison de versions et les contrôles de preuves.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Les tests utilisent des doubles pour les modèles et certaines intégrations.
Ils ne mesurent pas l’exactitude générale des réponses ou la qualité des encodeurs.

Des essais distincts avec Ollama local ont vérifié l’annulation suivie d’une nouvelle
requête. Une transcription audio synthétique en anglais a aussi été testée ; elle
ne mesure pas la précision du français ou de l’arabe, ni le microphone physique.

Docker était absent du poste de référence : ses commandes et leurs erreurs sont testées,
mais aucun succès réel de tests Python/JavaScript en conteneur n’est revendiqué.
Les essais personnalisés contrôlent des critères mécaniques et prévoient une revue
humaine. Les citations, transformations et résultats restent à examiner.
