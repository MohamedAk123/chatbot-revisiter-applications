"""Comparer deux instantanés textuels sans exécuter Git ou les fichiers."""

import difflib
import hashlib

from revisiter.projets import chemin_relatif
from revisiter.relations import analyser_impact, construire_relations
from revisiter.securite_analyse import examiner_fichier


def comparer_versions(avant, apres):
    for documents in (avant, apres):
        if not isinstance(documents, dict) or len(documents) > 300:
            raise ValueError('Un instantané de 300 fichiers maximum est attendu.')
        if any(not isinstance(c, str) for c in documents.values()):
            raise TypeError('Contenu textuel attendu.')
        if sum(len(c.encode('utf-8')) for c in documents.values()) > 20_000_000:
            raise ValueError('Instantané trop volumineux.')
        for nom, code in documents.items():
            chemin_relatif(nom)
            if not isinstance(code, str):
                raise TypeError('Contenu textuel attendu.')
    lignes, diffs, fichiers_changes = [], {}, set()
    for nom in sorted(set(avant) | set(apres)):
        ancien, nouveau = avant.get(nom), apres.get(nom)
        if ancien == nouveau:
            continue
        etat = 'ajouté' if ancien is None else 'supprimé' if nouveau is None else 'modifié'
        a = examiner_fichier(nom, ancien or '')
        b = examiner_fichier(nom, nouveau or '')
        delta = ''.join(difflib.unified_diff(a['text'].splitlines(keepends=True), b['text'].splitlines(keepends=True),
                                           fromfile='avant/' + nom, tofile='apres/' + nom))
        masque = bool(a['secrets'] or b['secrets'] or '[SECRET_MASQUE]' in (ancien or '') + (nouveau or ''))
        lignes.append({'file': nom, 'status': etat, 'redacted': masque,
                       'note': 'Comparaison partielle : valeurs sensibles masquées.' if masque else ''})
        diffs[nom] = delta[:60_000]
        fichiers_changes.add(nom)
    impactes, partiel = [], False
    for version, fichiers in (('après', apres), ('avant', avant)):
        protections = {n: examiner_fichier(n, c)['text'] for n, c in fichiers.items()}
        graphe = construire_relations(protections)
        candidats = [n for n in graphe['nodes'].values() if n['kind'] == 'function' and n['file'] in fichiers_changes]
        partiel = partiel or len(candidats) > 20 or graphe['limited']
        for noeud in candidats[:20]:
            impactes.append({'symbol': noeud['name'], 'reference': noeud['reference'], 'version': version,
                             'impact': analyser_impact(graphe, noeud['id'])})
    ajoutes = {n for n in apres if n not in avant}
    supprimes = {n for n in avant if n not in apres}
    deplacements = []
    for ancien in sorted(supprimes):
        egal = [n for n in ajoutes if hashlib.sha256(avant[ancien].encode()).digest() == hashlib.sha256(apres[n].encode()).digest()]
        if len(egal) == 1:
            deplacements.append({'from': ancien, 'to': egal[0], 'status': 'déplacement possible, contenu identique'})
    return {'files': lignes, 'diffs': diffs, 'impacts': impactes, 'possible_moves': deplacements, 'limited': partiel,
            'limits': 'Comparaison d’instantanés par chemin ; les fonctions des fichiers modifiés sont des candidates à examiner. '
                      'Les impacts sont calculés séparément avant/après et restent syntaxiques, bornés à quatre niveaux et 20 fonctions par version.'}
