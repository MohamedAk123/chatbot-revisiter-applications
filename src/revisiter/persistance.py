"""Instantané JSON limité aux champs utiles ; reconstruction du guide sans sérialiser le graphe."""

from copy import deepcopy
from uuid import uuid4

from revisiter.guide import CHAMPS_TEXTE, build_guided_plan_graph, valider_etapes
from revisiter.projets import FichierProjet, fichiers_importes

CHAMPS = ('messages', 'vue_ensemble', 'ameliorations', 'transformation', 'tests_regression', 'evaluation_rapport', 'benchmark_projet')


def instantane(documents, session):
    resultat = {'format': 1, 'documents': documents, 'state': {k: deepcopy(session[k]) for k in CHAMPS if k in session}}
    guide = session.get('guide')
    if guide:
        etat = guide['graphe'].get_state(guide['config']).values
        resultat['guide'] = {k: deepcopy(etat[k]) for k in ('recommandation', 'etapes', 'position', 'statuts', 'statut', 'observations') if k in etat}
    return resultat


def valider_instantane(donnees):
    if not isinstance(donnees, dict) or donnees.get('format') != 1 or not isinstance(donnees.get('documents'), dict):
        raise ValueError('Le format de cet espace enregistré n’est pas pris en charge.')
    documents = donnees['documents']
    if any(not isinstance(n, str) or not isinstance(c, str) for n, c in documents.items()):
        raise ValueError('Les fichiers enregistrés sont invalides.')
    importation = fichiers_importes([FichierProjet(n, c.encode('utf-8')) for n, c in documents.items()])
    if len(importation.fichiers) != len(documents):
        raise ValueError('Des fichiers enregistrés ne respectent plus les règles d’import.')
    state = donnees.get('state', {})
    if not isinstance(state, dict):
        raise ValueError('L’historique enregistré est invalide.')  # noqa: TRY004
    messages = state.get('messages', [])
    if not isinstance(messages, list) or any(not isinstance(m, dict) or m.get('role') not in {'user', 'assistant'} or not isinstance(m.get('content'), str) for m in messages):
        raise ValueError('La discussion enregistrée est invalide.')
    return {'documents': documents, 'state': {k: deepcopy(state[k]) for k in CHAMPS if k in state}, 'guide': donnees.get('guide')}


def restaurer_guide(donnees, extraits):
    if not isinstance(donnees, dict):
        raise ValueError('Le guide enregistré est invalide.')  # noqa: TRY004
    graphe = build_guided_plan_graph(extraits, donnees['recommandation'])
    config = {'configurable': {'thread_id': str(uuid4())}}
    sortie = graphe.invoke({}, config)
    etapes = valider_etapes({'etapes': [{k: e[k] for k in (*CHAMPS_TEXTE, 'references')} for e in donnees['etapes']]}, sortie['sources'])
    statuts, position, statut = donnees['statuts'], donnees['position'], donnees['statut']
    observations = donnees.get('observations', ['', '', ''])
    if not isinstance(position, int) or not 0 <= position <= 3 or len(statuts) != 3 or any(s not in {'a_faire', 'confirmee', 'sautee'} for s in statuts):
        raise ValueError('La progression enregistrée est invalide.')
    if statut not in {'en_cours', 'parcouru', 'abandonne'} or len(observations) != 3 or any(not isinstance(o, str) or len(o) > 2000 for o in observations):
        raise ValueError('Les observations enregistrées sont invalides.')
    if any(s == 'a_faire' for s in statuts[:position]) or any(s != 'a_faire' for s in statuts[position:]):
        raise ValueError('La progression est incohérente.')
    if (statut == 'parcouru' and position != 3) or (statut == 'en_cours' and position == 3):
        raise ValueError('L’état du guide est incohérent.')
    graphe.update_state(config, {'etapes': etapes, 'statuts': statuts, 'position': position,
        'statut': statut, 'observations': observations}, as_node='preparer_plan')
    if statut == 'abandonne':
        # Le point est restauré pour consultation uniquement ; reprendre_guide refuse ce statut.
        pass
    return {'graphe': graphe, 'config': config, 'revision': 0}
