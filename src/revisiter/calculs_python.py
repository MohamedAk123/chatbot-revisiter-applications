"""Déductions bornées pour les agrégations Python directes, sans eval ni import du projet."""

import ast
import math
import re
from pathlib import PurePosixPath

OPERATIONS = {
    'sum': (sum, 'additionne les valeurs'),
    'len': (len, 'compte les éléments'),
    'min': (min, 'sélectionne la plus petite valeur'),
    'max': (max, 'sélectionne la plus grande valeur'),
}


def _valeur(noeud):
    if isinstance(noeud, (ast.List, ast.Tuple)) and len(noeud.elts) <= 50:
        elements = [_valeur(n) for n in noeud.elts]
        if all(type(n) in {int, float} for n in elements):
            return elements if isinstance(noeud, ast.List) else tuple(elements)
    elif isinstance(noeud, ast.Constant) and type(noeud.value) in {int, float}:
        if math.isfinite(noeud.value) and abs(noeud.value) <= 10**12:
            return noeud.value
    elif isinstance(noeud, ast.UnaryOp) and isinstance(noeud.op, (ast.UAdd, ast.USub)):
        valeur = _valeur(noeud.operand)
        if type(valeur) in {int, float}:
            return -valeur if isinstance(noeud.op, ast.USub) else valeur
    raise ValueError('Valeur hors du sous-ensemble numérique autorisé.')


def _exemples(question, nom):
    resultats = []
    for m in re.finditer(r'(?<![\w.])' + re.escape(nom) + r'\s*\(', question):
        debut, profondeur = m.start(), 0
        for fin in range(m.end() - 1, min(len(question), debut + 500)):
            if question[fin] == '(':
                profondeur += 1
            elif question[fin] == ')':
                profondeur -= 1
                if profondeur == 0:
                    expression = question[debut:fin + 1]
                    try:
                        appel = ast.parse(expression, mode='eval').body
                        if (not isinstance(appel, ast.Call) or len(appel.args) != 1 or appel.keywords
                                or not isinstance(appel.func, ast.Name) or appel.func.id != nom):
                            return None
                        valeur = _valeur(appel.args[0])
                        if not isinstance(valeur, (list, tuple)):
                            return None
                        resultats.append((expression, valeur))
                    except (SyntaxError, ValueError, RecursionError):
                        return None
                    break
        else:
            return None
    return resultats if 1 <= len(resultats) <= 3 else None


def calcul_python(question, documents, budget=6000):
    """Uniquement un module isolé et une fonction return builtin(paramètre), avec exemples littéraux."""
    # Les demandes de changement, d'histoire, de schéma ou de tests restent sur leur parcours.
    if (not re.search(r'\b(retourn\w*|resultat|calcul\w*|verifi\w*|empech\w*|bloqu\w*|accepte\w*)\b', question['normalisee'])
            or re.search(r'\b(modifi\w*|transform\w*|corrig\w*|refactor\w*|amelior\w*|tests?|'
                         r'schema|diagramme|etape|preced\w*|comme|avant|encore|cette|cet|ces|cela|suite|'
                         r'optimis\w*|performances?|complexite|pourquoi|metier|securi\w*|vulnerab\w*|'
                         r'ecri\w*|gener\w*|propos\w*|ajout\w*|implement\w*|remplac\w*|chang\w*|versions?)\b', question['normalisee'])):
        return None
    texte = question['texte']
    appels = set(re.findall(r'(?<![\w.])([A-Za-z_]\w*)\s*\(', texte))
    if not appels:
        return None
    candidats = []
    for nom, contenu in documents.items():
        if not nom.lower().endswith(('.py', '.pyi')):
            continue
        if not any(re.search(r'\bdef\s+' + re.escape(n) + r'\s*\(', contenu) for n in appels):
            continue
        try:
            racine = ast.parse(contenu)
        except (SyntaxError, ValueError, RecursionError):
            continue
        corps = [n for n in racine.body if not (isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
        # Un module avec imports, réaffectations, décorateurs ou autres fonctions n'est pas interprété.
        if len(corps) != 1 or not isinstance(corps[0], ast.FunctionDef):
            continue
        f = corps[0]
        if (f.name not in appels
                or f.decorator_list or f.returns or f.args.defaults or f.args.kw_defaults
                or f.args.vararg or f.args.kwarg or f.args.kwonlyargs or f.args.posonlyargs
                or len(f.args.args) != 1 or f.args.args[0].annotation or f.name in OPERATIONS
                or f.args.args[0].arg in OPERATIONS):
            continue
        instructions = [n for n in f.body if not (isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
        if len(instructions) != 1 or not isinstance(instructions[0], ast.Return):
            continue
        retour = instructions[0].value
        if (not isinstance(retour, ast.Call) or not isinstance(retour.func, ast.Name)
                or retour.func.id not in OPERATIONS or len(retour.args) != 1 or retour.keywords
                or not isinstance(retour.args[0], ast.Name) or retour.args[0].id != f.args.args[0].arg):
            continue
        exemples = _exemples(texte, f.name)
        if exemples is not None:
            candidats.append((nom, contenu, f, retour.func.id, exemples))
    precises = [c for c in candidats if c[0].casefold() in texte.casefold()]
    if not precises:
        precises = [c for c in candidats if re.search(
            r'(?<![\w./])' + re.escape(PurePosixPath(c[0]).name) + r'(?![\w./])', texte, re.IGNORECASE)]
    candidats = precises or candidats
    if len(candidats) != 1:
        return None
    nom, contenu, fonction, operation, exemples = candidats[0]
    # Une condition demandée n'est pas conclue seulement depuis un nom de fonction.
    negative = re.search(r'\bnegati\w*\b', question['normalisee'])
    if negative and any(re.search(r'\b' + mot + r'\b', question['normalisee'])
                        for mot in ('verifie', 'empeche', 'bloque', 'accepte')) and not any(
            any(n < 0 for n in valeurs) for _, valeurs in exemples):
        return None
    reference = f'{nom}:L{fonction.lineno}-L{fonction.end_lineno}'
    lignes = contenu.splitlines()[fonction.lineno - 1:fonction.end_lineno]
    contexte = f'[{reference}]\n' + '\n'.join(lignes)
    if len(contexte) > budget:
        return None
    resultats = []
    try:
        for expression, valeurs in exemples:
            valeur = OPERATIONS[operation][0](valeurs)
            if type(valeur) not in {int, float} or not math.isfinite(valeur) or abs(valeur) > 10**12:
                return None
            resultats.append(f'`{expression}` donne **{valeur!r}** [{reference}].')
    except (ValueError, TypeError, OverflowError):
        return None
    description = (f'`{fonction.name}` {OPERATIONS[operation][1]} avec `{operation}({fonction.args.args[0].arg})` '
                   f'[{reference}].')
    if negative and any(any(n < 0 for n in valeurs) for _, valeurs in exemples):
        description += f' Cette définition ne contient pas de contrôle rejetant les valeurs négatives [{reference}].'
    reponse = description + '\n\n' + ' '.join(resultats)
    reponse += '\n\nCalcul contrôlé avec les fonctions intégrées Python standard, sans importer ni lancer le projet.'
    return {'direct_answer': reponse, 'sources': [reference], 'context': contexte,
            'calculation_verified': True}
