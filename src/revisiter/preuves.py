"""Contrôles limités de faits : les autres affirmations restent à relire."""

import re

from revisiter.calculs_python import calcul_python
from revisiter.recherche_code import normaliser
from revisiter.relations import construire_relations
from revisiter.robustesse import reference_couverte

APPEL = re.compile(r'`?([A-Za-z_]\w*)`?\s+appelle\s+(?:la fonction\s+)?`?([A-Za-z_]\w*)`?', re.IGNORECASE)
CALCUL = re.compile(r'([A-Za-z_]\w*\(\s*\[[^\]\r\n]{1,300}\]\s*\))`?\s+(?:donne|retourne|vaut)\s+\*{0,2}(-?\d+(?:\.\d+)?)', re.IGNORECASE)


def examiner_reponse(texte, sources, documents, *, methode=None, structures=None):
    controles = []
    citations = re.findall(r'\[([^\]\n]+:L\d+(?:-L\d+)?)\]', texte)
    for reference in dict.fromkeys(citations):
        controles.append({'claim': reference, 'kind': 'plage de citation',
                          'status': 'couverte' if reference_couverte(reference, sources) else 'hors contexte'})
    appels = APPEL.findall(texte)[:12]
    if appels and documents:
        graphe = construire_relations(documents, structures)
        noeuds = graphe['nodes']
        for appelant, appele in appels:
            liens = [e for e in graphe['edges'] if e['kind'] == 'call'
                     and noeuds[e['from']]['name'].split('.')[-1] == appelant
                     and noeuds[e['to']]['name'].split('.')[-1] == appele]
            surs = [e for e in liens if e['confidence'] == 'syntaxique']
            controles.append({'claim': appelant + ' appelle ' + appele, 'kind': 'appel local',
                              'status': 'confirmé syntaxiquement' if surs else 'non confirmé',
                              'references': list(dict.fromkeys(e['reference'] for e in surs))})
    for expression, annonce in CALCUL.findall(texte)[:3]:
        question = 'Quel est le résultat de ' + expression + ' ?'
        calcul = calcul_python({'texte': question, 'normalisee': normaliser(question)}, documents)
        if calcul:
            resultat = re.search(r'\bdonne\s+\*\*([^*]+)\*\*', calcul['direct_answer'])
            if resultat:
                try:
                    egal = float(resultat[1]) == float(annonce)
                except ValueError:
                    continue
                controles.append({'claim': expression + ' donne ' + annonce, 'kind': 'calcul contrôlé',
                                  'status': 'confirmé' if egal else 'contredit',
                                  'references': calcul['sources']})
    hypotheses = [p.strip()[:350] for p in re.split(r'\n+|(?<=[.!?])\s+', texte)
                  if re.search(r'\b(peut|pourrait|probablement|hypothèse|hypothese)\b', p, re.IGNORECASE)][:8]
    return {'scope': 'Analyse syntaxique déterministe' if methode == 'static' else 'Réponse générée à relire',
            'checks': controles[:40], 'hypotheses': hypotheses,
            'contradiction': any(c['status'] in {'contredit', 'hors contexte'} for c in controles),
            'limits': 'Les contrôles portent sur les plages, appels locaux résolus et calculs autorisés reconnus. '
                      'Ils ne prouvent pas toutes les affirmations ni le comportement à l’exécution.'}
