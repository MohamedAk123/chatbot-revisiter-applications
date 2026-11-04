"""Jeux de qualité sur le code choisi et comparaison de rapports comparables."""

import hashlib
import json

from revisiter.evaluation import evaluer_cas, rapport
from revisiter.lecteurs import preparer_extraits
from revisiter.projets import chemin_relatif
from revisiter.robustesse import reference_couverte
from revisiter.securite_analyse import proteger_documents


def valider_jeu(brut):
    if isinstance(brut, bytes):
        if len(brut) > 5_000_000:
            raise ValueError('Jeu trop volumineux : maximum 5 Mo.')
        brut = json.loads(brut.decode('utf-8-sig'))
    if not isinstance(brut, dict) or brut.get('format') != 1:
        raise ValueError('Format de jeu invalide.')
    documents, cas = brut.get('documents'), brut.get('cases')
    if not isinstance(documents, dict) or not 1 <= len(documents) <= 100 or not isinstance(cas, list) or not 1 <= len(cas) <= 50:
        raise ValueError('Le jeu attend 1 à 100 fichiers et 1 à 50 questions.')
    if any(not isinstance(c, str) for c in documents.values()) or sum(len(c.encode()) for c in documents.values()) > 4_000_000:
        raise ValueError('Le code du jeu dépasse la limite de 4 Mo.')
    for nom in documents:
        chemin_relatif(nom)
    protection = proteger_documents(documents)
    if protection['findings'] or len(protection['documents']) != len(documents):
        raise ValueError('Retirez les secrets et instructions suspectes du jeu de qualité.')
    sources = [p.reference for nom, code in documents.items() for p in preparer_extraits(nom, code)]
    ids, valides = set(), []
    for c in cas:
        if not isinstance(c, dict) or c.get('kind') not in {'static', 'llm'}:
            raise ValueError('Chaque question doit préciser kind : static ou llm.')
        identifiant, question, rubrique = c.get('id'), c.get('question'), c.get('rubric')
        if (not isinstance(identifiant, str) or not 1 <= len(identifiant) <= 80 or identifiant in ids
                or not isinstance(question, str) or not 1 <= len(question) <= 1500
                or not isinstance(rubrique, str) or not 1 <= len(rubrique) <= 1000):
            raise ValueError('Identifiant, question ou critère de revue invalide.')
        ids.add(identifiant)
        mots, refs = c.get('contains', []), c.get('sources', [])
        if (not isinstance(mots, list) or len(mots) > 30 or any(not isinstance(m, str) or not 1 <= len(m) <= 200 for m in mots)
                or not isinstance(refs, list) or len(refs) > 30 or any(not isinstance(r, str) or not reference_couverte(r, sources) for r in refs)):
            raise ValueError('Les mots ou références attendus sont invalides ou hors des fichiers du jeu.')
        if not mots and not refs:
            raise ValueError('Au moins un mot ou une référence attendue est nécessaire.')
        valides.append({k: c[k] for k in ('id', 'question', 'kind', 'rubric', 'contains', 'sources') if k in c})
    jeu = {'format': 1, 'name': str(brut.get('name', 'Mon projet'))[:100], 'documents': documents, 'cases': valides}
    empreinte = hashlib.sha256(json.dumps(jeu, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**jeu, 'dataset_sha256': empreinte}


def modele_jeu(documents):
    if not documents:
        raise ValueError('Choisissez des fichiers pour créer le jeu.')
    if proteger_documents(documents)['findings']:
        raise ValueError('Retirez les secrets et instructions suspectes avant de créer le jeu de qualité.')
    nom = min(documents)
    sources = preparer_extraits(nom, documents[nom])
    return {'format': 1, 'name': 'Qualité de mon projet', 'documents': documents,
            'cases': [{'id': 'roles-fichiers', 'kind': 'static',
                       'question': 'Quel est le rôle de chacun des fichiers importés ?',
                       'contains': [nom], 'sources': [sources[0].reference] if sources else [],
                       'rubric': 'Relire la description et vérifier les faits dans le code ; adapter les critères au projet.'}]}


def evaluer_jeu(jeu, modele=None, identite_modele='sans modèle', progression=None):
    jeu = valider_jeu(jeu)
    resultats = []
    for i, cas in enumerate(jeu['cases']):
        resultat = evaluer_cas({**cas, 'documents': jeu['documents']}, modele)
        resultat['review_required'] = 'answer' in resultat
        resultats.append(resultat)
        if progression:
            progression((i + 1) / len(jeu['cases']))
    return {**rapport(resultats), 'format': 1, 'dataset_sha256': jeu['dataset_sha256'],
            'model': identite_modele, 'reviews': {r['id']: 'à relire' for r in resultats if 'answer' in r}, 'name': jeu['name']}


def comparer_rapports(avant, apres):
    for r in (avant, apres):
        if not isinstance(r, dict) or not isinstance(r.get('cases'), list) or len(r['cases']) > 50:
            raise ValueError('Rapport de qualité invalide.')
        ids = [c.get('id') for c in r['cases'] if isinstance(c, dict)]
        if len(ids) != len(r['cases']) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Cas du rapport invalides ou dupliqués.')
        if not isinstance(r.get('reviews', {}), dict):
            raise TypeError('Revues du rapport invalides.')
    if avant.get('dataset_sha256') != apres.get('dataset_sha256') or not avant.get('dataset_sha256'):
        raise ValueError('Les rapports doivent porter sur exactement le même jeu et les mêmes fichiers.')
    anciens = {c['id']: c for c in avant.get('cases', [])}
    nouveaux = {c['id']: c for c in apres.get('cases', [])}
    if set(anciens) != set(nouveaux):
        raise ValueError('Les rapports ne contiennent pas les mêmes cas.')
    lignes = []
    for identifiant, c in nouveaux.items():
        ancien = anciens[identifiant]
        lignes.append({'id': identifiant, 'before': ancien.get('checks_passed'), 'after': c.get('checks_passed'),
                       'regression': ancien.get('checks_passed') is True and c.get('checks_passed') is False,
                       'duration_before_s': ancien.get('total_s'), 'duration_after_s': c.get('total_s'),
                       'review_before': avant.get('reviews', {}).get(identifiant),
                       'review_after': apres.get('reviews', {}).get(identifiant)})
    return lignes
