"""Relations syntaxiques bornées : les liens possibles sont séparés des résolutions locales."""

import ast
import re
from collections import defaultdict, deque
from pathlib import PurePosixPath

from revisiter.structures import analyser_structure

MAX_SYMBOLS = 3000
MAX_EDGES = 10000


def _module(nom):
    return str(PurePosixPath(nom).with_suffix('')).replace('/', '.').removesuffix('.__init__')


def construire_relations(documents, analyses=None):
    analyses = analyses or {n: analyser_structure(n, c) for n, c in documents.items()}
    noeuds, liens, exclus = {}, [], []
    par_nom, par_fichier = defaultdict(list), defaultdict(list)
    modules = {_module(n): n for n in documents}

    def resoudre_module(module):
        if module in modules:
            return modules[module]
        candidats = [f for m, f in modules.items() if m.endswith('.' + module)]
        return candidats[0] if len(candidats) == 1 else None
    for nom, analyse in sorted(analyses.items()):
        noeuds['f:' + nom] = {'id': 'f:' + nom, 'file': nom, 'name': nom, 'kind': 'file', 'reference': f'{nom}:L1-L1'}
        if analyse['status'] != 'ok':
            exclus.append(nom)
            continue
        for s in analyse['symbols']:
            if len(noeuds) >= MAX_SYMBOLS:
                break
            identifiant = f"s:{nom}:{s['name']}:{s['line']}"
            noeud = {**s, 'file': nom, 'id': identifiant}
            noeuds[identifiant] = noeud
            par_nom[s['name'].split('.')[-1]].append(noeud)
            par_fichier[nom].append(noeud)
    for nom, symboles in par_fichier.items():
        for symbole in symboles:
            classes = [s for s in symboles if s['kind'] == 'class' and s['id'] != symbole['id']
                and s['line'] <= symbole['line'] and s.get('end_line', s['line']) >= symbole.get('end_line', symbole['line'])
                and (symbole['kind'] != 'class' or (s['line'], s.get('end_line')) != (symbole['line'], symbole.get('end_line')))]
            classes.sort(key=lambda s: s.get('end_line', s['line']) - s['line'])
            # Deux classes sur les mêmes lignes ne suffisent pas à attribuer une méthode.
            unique = classes and (len(classes) == 1 or classes[0].get('end_line', 0) - classes[0]['line'] < classes[1].get('end_line', 0) - classes[1]['line'])
            parent = classes[0]['id'] if unique else 'f:' + nom
            liens.append({'from': parent, 'to': symbole['id'], 'kind': 'contains', 'confidence': 'syntaxique', 'reference': symbole['reference']})
    for nom, code in documents.items():
        analyse = analyses.get(nom, {})
        if analyse.get('status') != 'ok':
            continue
        alias, imports = {}, set()
        if nom.endswith(('.py', '.pyi')):
            racine = ast.parse(code)
            package = _module(nom).split('.') if PurePosixPath(nom).stem == '__init__' else _module(nom).split('.')[:-1]
            for item in ast.walk(racine):
                if isinstance(item, ast.ImportFrom):
                    base = package[:len(package) - item.level + 1] if item.level else []
                    module = '.'.join(base + ([item.module] if item.module else []))
                    for a in item.names:
                        cible_fichier = resoudre_module(module)
                        cible_module = resoudre_module(module + '.' + a.name)
                        alias[a.asname or a.name] = (cible_module or cible_fichier, None if cible_module else a.name)
                        if cible_module or cible_fichier:
                            imports.add((cible_module or cible_fichier, item.lineno))
                elif isinstance(item, ast.Import):
                    for a in item.names:
                        cible = resoudre_module(a.name)
                        if cible:
                            alias[a.asname or a.name] = (cible, None)
                            imports.add((cible, item.lineno))
            appels = []
            for item in ast.walk(racine):
                if isinstance(item, ast.Call):
                    fonction = ast.unparse(item.func)
                    if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', fonction):
                        appels.append((fonction, item.lineno))
        else:
            appels = [(a['name'], a['line']) for a in analyse.get('calls', [])]
            # Résolution bornée des imports relatifs JS/TS ; les autres liens restent des candidats.
            for item in re.finditer(r'\bimport\s+\{([^}]+)\}\s+from\s*[\'"]([^\'"]+)[\'"]', code):
                chemin = item[2]
                if not chemin.startswith('.'):
                    continue
                parties = list(PurePosixPath(nom).parent.parts)
                for p in chemin.split('/'):
                    if p == '..' and parties:
                        parties.pop()
                    elif p not in {'.', '..', ''}:
                        parties.append(p)
                base = '/'.join(parties)
                candidats = [base, base + '.ts', base + '.tsx', base + '.js', base + '/index.ts', base + '/index.js']
                cibles = [p for p in candidats if p in documents]
                if len(cibles) == 1:
                    for membre in item[1].split(','):
                        noms = re.split(r'\s+as\s+', membre.strip())
                        if all(re.fullmatch(r'[A-Za-z_]\w*', n) for n in noms):
                            alias[noms[-1]] = (cibles[0], noms[0])
                    imports.add((cibles[0], code.count('\n', 0, item.start()) + 1))
        for cible, ligne in sorted(imports):
            liens.append({'from': 'f:' + nom, 'to': 'f:' + cible, 'kind': 'import', 'confidence': 'syntaxique',
                          'reference': f'{nom}:L{ligne}-L{ligne}'})
        for appel, ligne in appels:
            dernier = re.split(r'\.|::|->', appel)[-1]
            locaux = [s for s in par_fichier[nom] if s['kind'] == 'function' and s['line'] <= ligne <= s['end_line']]
            parent = min(locaux, key=lambda s: s['end_line'] - s['line'])['id'] if locaux else 'f:' + nom
            premier = appel.split('.')[0]
            confiance = 'possible'
            if premier in alias and alias[premier][0]:
                fichier, membre = alias[premier]
                cible_nom = membre if '.' not in appel and membre else dernier
                cibles = [s for s in par_fichier[fichier] if s['name'].split('.')[-1] == cible_nom]
                confiance = 'syntaxique'
            else:
                cibles = [s for s in par_fichier[nom] if s['kind'] == 'function' and s['name'].split('.')[-1] == dernier]
                if len(cibles) == 1 and ('.' not in appel or appel.startswith(('self.', 'this.'))):
                    confiance = 'syntaxique'
                else:
                    cibles = par_nom.get(dernier, [])
            for cible in cibles[:5]:
                if len(liens) >= MAX_EDGES:
                    break
                liens.append({'from': parent, 'to': cible['id'], 'kind': 'call', 'confidence': confiance,
                              'reference': f'{nom}:L{ligne}-L{ligne}'})
    return {'nodes': noeuds, 'edges': liens[:MAX_EDGES], 'excluded': exclus,
            'limited': len(noeuds) >= MAX_SYMBOLS or len(liens) >= MAX_EDGES}


def analyser_impact(graphe, cible, *, profondeur=4):
    if cible not in graphe['nodes']:
        raise ValueError('Élément absent de la cartographie.')
    inverses = defaultdict(list)
    for lien in graphe['edges']:
        if lien['kind'] == 'call':
            inverses[lien['to']].append(lien)
    racines, attente = {cible}, [cible]
    if graphe['nodes'][cible]['kind'] in {'file', 'class'}:
        while attente and len(racines) < MAX_SYMBOLS:
            courant = attente.pop()
            for lien in graphe['edges']:
                if lien['kind'] == 'contains' and lien['from'] == courant and lien['to'] not in racines:
                    racines.add(lien['to'])
                    attente.append(lien['to'])
    file = deque((n, 0, False) for n in racines)
    visites, touches = {(n, False) for n in racines}, []
    while file and len(touches) < 300:
        courant, distance, possible = file.popleft()
        if distance >= profondeur:
            continue
        for lien in inverses[courant]:
            incertain = possible or lien['confidence'] == 'possible'
            identifiant = (lien['from'], incertain)
            if identifiant in visites:
                continue
            visites.add(identifiant)
            touches.append({**graphe['nodes'][lien['from']], 'distance': distance + 1,
                            'confidence': 'possible' if incertain else 'syntaxique', 'call_reference': lien['reference']})
            file.append((lien['from'], distance + 1, incertain))
    fichier = graphe['nodes'][cible]['file']
    importateurs = [l for l in graphe['edges'] if l['kind'] == 'import' and l['to'] == 'f:' + fichier]
    return {'target': graphe['nodes'][cible], 'callers': touches, 'importers': importateurs,
            'files': sorted({n['file'] for n in touches}),
            'limits': 'Résolution syntaxique, sans exécution. Les alias réaffectés, appels dynamiques, types et chemins d’exécution restent à vérifier. Parcours limité à quatre niveaux.'}


def texte_impact(resultat):
    cible = resultat['target']
    lignes = [f"Impact à examiner pour `{cible['name']}` [{cible['reference']}] :"]
    for appel in resultat['callers']:
        lignes.append(f"- {appel['confidence']} · niveau {appel['distance']} · `{appel['name']}` [{appel['call_reference']}]")
    if not resultat['callers']:
        lignes.append('Aucun appelant repéré dans les fichiers analysés ; cela ne prouve pas l’absence d’utilisation.')
    for lien in resultat['importers']:
        lignes.append(f"- Import du fichier, à examiner : [{lien['reference']}]")
    return '\n\n'.join(lignes) + '\n\n' + resultat['limits']


def schema_relations(graphe, cible=None):
    """DOT produit uniquement depuis des nœuds bornés, avec chaînes échappées."""
    import json

    liens = graphe['edges']
    if cible:
        voisins = {cible}
        for lien in liens:
            if cible in {lien['from'], lien['to']}:
                voisins.update((lien['from'], lien['to']))
        liens = [l for l in liens if l['from'] in voisins and l['to'] in voisins]
    liens = liens[:100]
    ids = list(dict.fromkeys(n for l in liens for n in (l['from'], l['to'])))[:60]
    codes = {identifiant: 'n' + str(i) for i, identifiant in enumerate(ids)}
    sortie = ['digraph G {', 'rankdir=LR; node [shape=box, style=rounded];']
    for identifiant in ids:
        noeud = graphe['nodes'][identifiant]
        sortie.append(codes[identifiant] + ' [label=' + json.dumps(noeud['name'][:100], ensure_ascii=False) + '];')
    for lien in liens:
        if lien['from'] in codes and lien['to'] in codes:
            style = 'dashed' if lien['confidence'] == 'possible' else 'solid'
            sortie.append(f"{codes[lien['from']]} -> {codes[lien['to']]} [label=\"{lien['kind']}\", style={style}];")
    return '\n'.join(sortie + ['}'])
