"""Résoudre un suivi court depuis des sujets attestés, sans mémoire entre projets."""

import re

from revisiter.structures import analyser_structure

SUIVI = re.compile(r'(?i)\b(cette fonction|ce fichier|cette méthode|cette methode|celle-ci|celui-ci|'
                   r'corrige[- ]la|explique (?:davantage|plus)|et (?:les|ses) tests|et son impact|'
                   r'améliore[- ]la|ameliore[- ]la|refais[- ]le)\b')


def resoudre_suivi(question, historique, documents, structures=None):
    if not SUIVI.search(question):
        return {'question': question, 'subject': None, 'clarification': None}
    structures = structures or {n: analyser_structure(n, c) for n, c in documents.items()}
    symboles = [(nom, s['name']) for nom, a in structures.items() for s in a['symbols']
                if s['kind'] in {'function', 'class'}]

    def sujets(texte):
        fichiers = [n for n in documents if n in texte]
        trouves = [(n, s) for n, s in symboles if (not fichiers or n in fichiers)
                   and (re.search(r'`' + re.escape(s) + r'`', texte)
                        or len(s) >= 3 and re.search(r'(?<!\w)' + re.escape(s) + r'(?!\w)', texte))]
        return list(dict.fromkeys(trouves)) or [(n, None) for n in fichiers]

    candidats = sujets(question)
    if not candidats:
        for message in reversed(historique[-8:]):
            if message.get('role') == 'user':
                candidats = sujets(message.get('content', ''))
                if candidats:
                    break
    if not candidats and historique:
        dernier = historique[-1]
        candidats = sujets(dernier.get('content', ''))
    if len(candidats) != 1:
        choix = ', '.join(n + (' · ' + s if s else '') for n, s in candidats[:6])
        return {'question': question, 'subject': None,
                'clarification': 'Quel fichier ou quelle fonction veux-tu reprendre ?'
                + (' Sujets possibles : ' + choix + '.' if choix else ' Précise son nom pour continuer.')}
    fichier, symbole = candidats[0]
    sujet = fichier + (' · ' + symbole if symbole else '')
    contraintes = [m['content'][:400] for m in historique[-6:] if m.get('role') == 'user'
                   and re.search(r'(?i)\b(sans modifier|ne (?:change|modifie) pas|conserve|préserve|preserve)\b', m.get('content', ''))]
    supplement = '\nSujet repris explicitement : ' + sujet + '.'
    if contraintes:
        supplement += '\nContraintes déjà demandées : ' + ' ; '.join(contraintes[-2:])
    return {'question': question + supplement, 'subject': sujet, 'clarification': None}
