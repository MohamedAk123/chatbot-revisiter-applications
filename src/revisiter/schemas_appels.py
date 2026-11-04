"""Schéma d'appels locaux issu du graphe syntaxique, sans génération de texte."""

import re
import unicodedata
from collections import deque
from pathlib import PurePosixPath

from revisiter.schemas import MAX_LIENS, MAX_NOEUDS, ErreurSchema, valider_schema


def est_demande_appels(question):
    texte = ''.join(c for c in unicodedata.normalize('NFKD', question.casefold())
                    if not unicodedata.combining(c))
    return bool(re.search(r'\b(appels?|appelants?|appelle(?:nt)?|chaine d.appels)\b', texte)) and not re.search(
        r'\b(conditions?|branches?|algorithme|sequence|temporel|risques?|amelior\w*|transform\w*)\b', texte)


def schema_appels(question, graphe, documents, budget=6000):
    """Citer déclarations et appels résolus ; ne jamais inventer les liens ambigus."""
    if not est_demande_appels(question):
        return None
    fonctions = {i: n for i, n in graphe['nodes'].items() if n['kind'] == 'function'}
    par_nom = {}
    question_noms = question
    fichiers_precises = {n for n in documents if n.casefold() in question.casefold()}
    if not fichiers_precises:
        fichiers_precises = {n for n in documents if re.search(
            r'(?<![\w./])' + re.escape(PurePosixPath(n).name) + r'(?![\w./])', question, re.IGNORECASE)}
    for fichier in fichiers_precises:
        question_noms = re.sub(re.escape(fichier), '', question_noms, flags=re.IGNORECASE)
        question_noms = re.sub(re.escape(PurePosixPath(fichier).name), '', question_noms, flags=re.IGNORECASE)
    for i, n in fonctions.items():
        nom = n['name'].split('.')[-1]
        if re.search(r'(?<!\w)' + re.escape(n['name']) + r'(?!\w)', question_noms) or re.search(
                r'(?<!\w)' + re.escape(nom) + r'(?!\w)', question_noms):
            par_nom.setdefault(nom, []).append(i)
    if not par_nom:
        return None
    racines = set()
    for nom, ids in par_nom.items():
        precises = [i for i in ids if fonctions[i]['file'] in fichiers_precises]
        candidats = precises or ids
        if len(candidats) > 1:
            raise ErreurSchema(f'Plusieurs fonctions portent le nom {nom}. Précisez leur fichier pour créer le schéma.')
        racines.update(candidats)
    if len(racines) > MAX_NOEUDS:
        raise ErreurSchema('Le schéma est limité à huit fonctions. Réduisez les noms demandés.')
    # Les noms de fonctions explicitement cités mais absents ne sont pas ignorés.
    explicites = re.findall(r'`([^`]+)`', question)
    entre = re.search(r'\bentre\s+(.+?)(?:[!?;]|\.(?=\s|$)|\bavec\b|\ben citant\b|$)', question, re.IGNORECASE)
    if entre:
        for fragment in re.split(r',|\bet\b', entre[1]):
            fragment = re.sub(r'^\s*(?:les?\s+)?(?:fonctions?\s+)?', '', fragment).strip(' `')
            if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', fragment):
                explicites.append(fragment)
    for nom in explicites:
        if any(nom.casefold() in {n.casefold(), PurePosixPath(n).name.casefold()} for n in documents):
            continue
        if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', nom) and not any(
                nom in {n['name'], n['name'].split('.')[-1]} for n in fonctions.values()):
            raise ErreurSchema(f'La fonction {nom} n’est pas repérée dans les fichiers analysés.')

    arcs = [l for l in graphe['edges'] if l['kind'] == 'call' and l['confidence'] == 'syntaxique'
            and l['from'] in fonctions and l['to'] in fonctions]
    suivant = {}
    for lien in arcs:
        suivant.setdefault(lien['from'], []).append(lien)
    def cle(i):
        return fonctions[i]['file'], fonctions[i]['line'], fonctions[i]['name']

    selection, attente, limite = set(racines), deque(sorted(racines, key=cle)), False
    while attente:
        for lien in suivant.get(attente.popleft(), []):
            if lien['to'] not in selection:
                if len(selection) >= MAX_NOEUDS:
                    limite = True
                    continue
                selection.add(lien['to'])
                attente.append(lien['to'])
    liens, vus = [], set()
    for lien in arcs:
        signature = (lien['from'], lien['to'], lien['reference'])
        if lien['from'] in selection and lien['to'] in selection and signature not in vus:
            vus.add(signature)
            liens.append(lien)
    if len(liens) > MAX_LIENS:
        limite = True
        liens = liens[:MAX_LIENS]
    # Appelants avant appelés ; les cycles conservent leurs flèches explicites.
    indegres = {i: sum(l['to'] == i for l in liens) for i in selection}
    prets, ordre = deque(sorted((i for i in selection if not indegres[i]), key=cle)), []
    while prets:
        courant = prets.popleft()
        ordre.append(courant)
        for lien in liens:
            if lien['from'] == courant:
                indegres[lien['to']] -= 1
                if indegres[lien['to']] == 0:
                    prets.append(lien['to'])
    ordre.extend(sorted(selection - set(ordre), key=cle))

    references, preuves = [], []

    def source(reference):
        if reference not in references:
            m = re.fullmatch(r'(.+):L(\d+)-L(\d+)', reference)
            if not m or m[1] not in documents:
                raise ErreurSchema('Une source du graphe ne correspond pas aux fichiers importés.')
            lignes = documents[m[1]].splitlines()
            debut, fin = int(m[2]), int(m[3])
            if not 1 <= debut <= fin <= len(lignes):
                raise ErreurSchema('Une source du graphe dépasse les lignes du fichier.')
            bloc = f'[{reference}]\n' + '\n'.join(lignes[debut - 1:fin])
            if len('\n\n'.join([*preuves, bloc])) > budget:
                raise ErreurSchema('Les preuves du schéma dépassent le budget. Ciblez moins de fonctions.')
            references.append(reference)
            preuves.append(bloc)
        return references.index(reference) + 1

    doublons = {fonctions[i]['name'] for i in ordre if sum(
        fonctions[j]['name'] == fonctions[i]['name'] for j in ordre) > 1}
    noeuds = []
    for i in ordre:
        n = fonctions[i]
        libelle = (n['name'][:45] + ' · ' + PurePosixPath(n['file']).name[:30]
                   if n['name'] in doublons else n['name'][:80])
        noeuds.append({'libelle': libelle, 'genre': 'traitement', 'sources': [source(n['reference'])]})
    positions = {i: index for index, i in enumerate(ordre, 1)}
    fleches = []
    for l in sorted(liens, key=lambda l: (positions[l['from']], positions[l['to']], l['reference'])):
        refs = [source(l['reference'])]
        # L'import explique notamment l'alias total -> total_panier dans Atlas.
        for imp in graphe['edges']:
            if len(refs) >= 3:
                break
            if (imp['kind'] == 'import' and imp['from'] == 'f:' + fonctions[l['from']]['file']
                    and imp['to'] == 'f:' + fonctions[l['to']]['file']):
                numero = source(imp['reference'])
                if numero not in refs and len(refs) < 3:
                    refs.append(numero)
        fleches.append({'de': positions[l['from']], 'vers': positions[l['to']],
                        'libelle': 'appelle', 'sources': refs})
    explication = ("Schéma syntaxique, sans exécution. Il n’établit pas l’ordre d’exécution "
                   "et omet les appels externes, dynamiques ou ambigus.")
    if not liens:
        explication += ' Aucun appel local résolu entre les fonctions représentées.'
    if limite or graphe.get('limited'):
        explication += ' Schéma partiel : limite atteinte.'
    if graphe.get('excluded'):
        explication += f" {len(graphe['excluded'])} fichier(s) non analysé(s)."
    resultat = valider_schema({'titre': 'Appels entre fonctions', 'explication': explication,
                              'noeuds': noeuds, 'liens': fleches}, references)
    return {'direct_schema': resultat, 'sources': references, 'context': '\n\n'.join(preuves)}
