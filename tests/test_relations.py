from revisiter.relations import analyser_impact, construire_relations, texte_impact


def test_alias_imports_appels_transitifs_cycles_et_references():
    docs = {'lib/calcul.py': 'def total(prix):\n    return sum(prix)\n',
        'service.py': 'from lib.calcul import total as somme\ndef commander(prix):\n    return somme(prix)\n',
        'ui.py': 'import service as svc\ndef cli(prix):\n    return svc.commander(prix)\n'}
    graphe = construire_relations(docs)
    cible = next(n['id'] for n in graphe['nodes'].values() if n['name'] == 'total')
    impact = analyser_impact(graphe, cible)
    assert {n['name'] for n in impact['callers']} == {'commander', 'cli'}
    assert {n['confidence'] for n in impact['callers']} == {'syntaxique'}
    assert [n['distance'] for n in impact['callers']] == [1, 2]
    assert '[service.py:L3-L3]' in texte_impact(impact)


def test_homonymes_non_resolus_marques_possibles_et_commentaires_ignores():
    docs = {'a.py': 'def calcul():\n    return 1', 'b.py': 'def calcul():\n    return 2',
        'c.py': '# calcul()\ndef f(obj):\n    return obj.calcul()'}
    g = construire_relations(docs)
    appels = [l for l in g['edges'] if l['kind'] == 'call']
    assert len(appels) == 2 and all(l['confidence'] == 'possible' for l in appels)
    assert all(l['reference'] == 'c.py:L3-L3' for l in appels)


def test_typescript_import_relatif_et_alias():
    docs = {'src/calcul.ts': 'export function total(x: number) { return x; }',
        'src/ui.ts': "import {total as somme} from './calcul';\nexport function voir() { return somme(2); }"}
    g = construire_relations(docs)
    cible = next(n['id'] for n in g['nodes'].values() if n['name'] == 'total')
    resultat = analyser_impact(g, cible)
    assert resultat['callers'][0]['name'] == 'voir'
    assert resultat['callers'][0]['confidence'] == 'syntaxique'


def test_layout_src_resolu_sans_supposer_que_src_est_un_package_importable():
    docs = {'projet/src/app/calcul.py': 'def total(x):\n    return x',
        'projet/src/app/service.py': 'from app.calcul import total\ndef f(x):\n    return total(x)'}
    g = construire_relations(docs)
    cible = next(n['id'] for n in g['nodes'].values() if n['name'] == 'total')
    assert analyser_impact(g, cible)['callers'][0]['confidence'] == 'syntaxique'


def test_relations_de_contenance_et_impact_d_une_classe_et_de_son_fichier():
    docs = {'lib.py': 'class Panier:\n    def total(self):\n        return 1',
        'ui.py': 'from lib import Panier\ndef afficher(p):\n    return p.total()'}
    g = construire_relations(docs)
    classe = next(n['id'] for n in g['nodes'].values() if n['kind'] == 'class')
    assert any(l['kind'] == 'contains' and l['from'] == classe for l in g['edges'])
    assert analyser_impact(g, classe)['callers'][0]['name'] == 'afficher'
    assert analyser_impact(g, 'f:lib.py')['callers'][0]['name'] == 'afficher'
    compact = construire_relations({'A.java': 'class A { int f() { return 1; } } class B { int g() { return 2; } }'})
    classes = {n['id'] for n in compact['nodes'].values() if n['kind'] == 'class'}
    assert not any(l['kind'] == 'contains' and l['from'] in classes for l in compact['edges'])
