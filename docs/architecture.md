# Architecture actuelle

L’interface principale est `app.py`. Les modules de `src/revisiter` séparent la
lecture des fichiers, l’analyse, la recherche, les réponses et les espaces persistants.

1. Les lecteurs importent les fichiers autorisés en conservant les références de lignes.
   L’interface accepte fichiers, dossiers, ZIP et dépôts GitHub publics avec des limites.
2. Le filtre masque les secrets détectés et repère certaines instructions suspectes
   avant indexation et sauvegarde. Sa détection par motifs reste partielle.
3. Les analyseurs extraient symboles et relations syntaxiques. Des routes déterministes
   décrivent les fichiers, les appels et certains calculs.
4. La recherche hybride combine repérage lexical et encodeurs CamemBERT/BERT.
   Ces encodeurs ne génèrent pas les réponses.
5. `workflow.py` orchestre les réponses via LangGraph ; `ollama_client.py` assure
   l’intégration LangChain/Ollama. La file de génération est bornée et annulable.
6. `preuves.py` contrôle les citations et quelques affirmations reconnues.
   Cela ne constitue pas une preuve générale de correction sémantique.
7. Les recommandations et transformations sont des propositions à relire.
   Elles ne sont pas appliquées automatiquement aux fichiers importés.
8. La validation dynamique facultative exige Docker local, sans exécution du code
   importé sur l’hôte et sans repli si Docker manque.

Les espaces et comptes utilisent un stockage local exclu de Git. La dictée utilise
faster-whisper localement, puis demande la confirmation du texte transcrit.
La file de génération est propre à un processus Streamlit ; plusieurs instances
nécessiteraient une coordination partagée.

Les documents de planning et backlog conservent le cadrage initial.
