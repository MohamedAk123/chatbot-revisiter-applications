"""Calculs agrégés limités : exactitude, preuve et refus des cas non démontrés."""

import pytest
from test_rapidite import SansEncodeur
from test_recherche import RecordingModel
from test_roles_fichiers import ATLAS

from revisiter.calculs_python import calcul_python
from revisiter.lecteurs import preparer_extraits
from revisiter.recherche_code import normaliser
from revisiter.robustesse import controler_citations
from revisiter.workflow import build_question_graph

QUESTION = ('Le code de total_panier empêche-t-il les prix négatifs ? Justifie avec les lignes du fichier '
            'et explique le résultat de total_panier([10, -3]), sans exécuter le code.')


def calcul(question, documents, budget=6000):
    return calcul_python({'texte': question, 'normalisee': normaliser(question)}, documents, budget)


def test_atlas_calcule_sept_et_cite_la_definition_sans_modele_ni_encodeur():
    class SansModele:
        def stream(self, *args, **kwargs):
            raise AssertionError('Le calcul littéral simple ne doit pas utiliser Ollama.')

    chunks = [e for n, t in ATLAS.items() for e in preparer_extraits(n, t)]
    graphe = build_question_graph(chunks, SansEncodeur(), SansModele(), retrieval_mode='auto', documents=ATLAS)
    resultat = graphe.invoke({'question': QUESTION})
    assert '**7**' in resultat['answer']
    assert 'ne contient pas de contrôle rejetant' in resultat['answer']
    assert resultat['sources'] == ['app/panier.py:L1-L2']
    assert controler_citations(resultat['answer'], resultat['sources']) == 'checked'
    assert resultat['metrics']['generation_ms'] == 0
    assert resultat['metrics']['calculation_verified'] is True


@pytest.mark.parametrize(('operation', 'exemple', 'attendu'), [
    ('sum', '[10, -3]', '7'), ('sum', '[]', '0'), ('sum', '(2, 3)', '5'),
    ('len', '[10, -3]', '2'), ('min', '[10, -3]', '-3'), ('max', '[10, -3]', '10'),
])
def test_noms_arbitraires_et_operations_autorisees(operation, exemple, attendu):
    resultat = calcul(f'Quel résultat retourne agreger({exemple}) ?',
                      {'calcul.py': f'def agreger(elements):\n    return {operation}(elements)\n'})
    assert f'**{attendu}**' in resultat['direct_answer']
    assert resultat['sources'] == ['calcul.py:L1-L2']


@pytest.mark.parametrize('code', [
    'sum = lambda x: 123\ndef total_panier(prix):\n    return sum(prix)',
    'from autre import sum\ndef total_panier(prix):\n    return sum(prix)',
    '@decorateur\ndef total_panier(prix):\n    return sum(prix)',
    'def total_panier(prix=donnees()):\n    return sum(prix)',
    'def total_panier(prix):\n    if any(p < 0 for p in prix):\n        raise ValueError()\n    return sum(prix)',
    'def total_panier(sum):\n    return sum(sum)',
    'async def total_panier(prix):\n    return sum(prix)',
    'def total_panier(prix):\n    return fonction_inconnue(prix)',
])
def test_code_non_prouve_ou_builtin_masque_reste_au_modele(code):
    assert calcul(QUESTION, {'panier.py': code}) is None


@pytest.mark.parametrize('expression', [
    'total_panier([1] * 1000000000)', 'total_panier([__import__("os")])',
    'total_panier([10**100000])', 'total_panier([1e9999])', 'total_panier([True])',
    'total_panier([[1], [2]])', 'total_panier(prix)', 'total_panier(prix=[1, 2])',
    'objet.total_panier([1, 2])',
])
def test_expressions_non_litterales_ou_couteuses_ne_sont_pas_evaluees(expression):
    assert calcul('Quel résultat retourne ' + expression + ' ?', ATLAS) is None


def test_resultats_vides_erreurs_et_ambiguites_ne_sont_pas_inventes():
    assert calcul('Quel résultat retourne f([]) ?', {'a.py': 'def f(x):\n    return min(x)'}) is None
    assert calcul('Quel résultat retourne f([1, 2]) ?', {
        'a.py': 'def f(x):\n    return sum(x)', 'b.py': 'def f(x):\n    return len(x)'}) is None
    resultat = calcul('Dans a.py, quel résultat retourne f([1, 2]) ?', {
        'a.py': 'def f(x):\n    return sum(x)', 'b.py': 'def f(x):\n    return len(x)'})
    assert '**3**' in resultat['direct_answer']
    assert calcul(QUESTION, ATLAS, budget=10) is None


def test_demande_de_modification_ou_de_suivi_conserve_le_modele():
    for question in ['Corrige total_panier([10, -3]) pour empêcher les prix négatifs.',
                     'Calcule total_panier([10, -3]) comme avant.',
                     'Écris une version qui calcule total_panier([10, -3]).']:
        assert calcul(question, ATLAS) is None
    modele = RecordingModel()
    chunks = preparer_extraits('app/panier.py', ATLAS['app/panier.py'])
    graphe = build_question_graph(chunks, SansEncodeur(), modele, retrieval_mode='auto', documents=ATLAS)
    graphe.invoke({'question': 'Corrige total_panier([10, -3]) pour empêcher les prix négatifs.'})
    assert len(modele.calls) == 1


def test_plusieurs_exemples_references_exactes_et_aucun_code_du_projet_execute(tmp_path):
    question = 'Quel résultat retourne total_panier([10, -3]) et total_panier([]) ?'
    resultat = calcul(question, ATLAS)
    assert '**7**' in resultat['direct_answer'] and '**0**' in resultat['direct_answer']
    assert controler_citations(resultat['direct_answer'], resultat['sources']) == 'checked'
    marque = tmp_path / 'interdit.txt'
    code = f"open({str(marque)!r}, 'w').write('execute')\ndef total_panier(prix):\n    return sum(prix)"
    assert calcul(QUESTION, {'panier.py': code}) is None
    assert not marque.exists()


def test_aucun_verdict_negatif_depuis_un_seul_exemple_positif():
    assert calcul('Le code empêche les prix négatifs ? Quel résultat pour total_panier([10, 3]) ?', ATLAS) is None
