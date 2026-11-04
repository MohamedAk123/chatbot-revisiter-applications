import json

import pytest

from revisiter.tests_explicites import construire_tests_explicites

TRANSFORMATION = {'file': 'stock.py', 'code': 'stock = {"stylo": 5}\ndef retirer(nom, quantite):\n    if quantite > stock[nom]:\n        raise ValueError("insuffisant")\n    stock[nom] -= quantite\n    return stock[nom]'}


def cas():
    return [{'name': 'normal', 'function': 'retirer', 'setup': {'stock': {'stylo': 5}}, 'args': ['stylo', 2],
        'expected': 3, 'after': {'stock': {'stylo': 3}}},
        {'name': 'depassement', 'function': 'retirer', 'setup': {'stock': {'stylo': 5}}, 'args': ['stylo', 6],
        'raises': 'ValueError', 'after': {'stock': {'stylo': 5}}}]


def test_construction_deterministe_avec_reinitialisation_du_vrai_module_et_assertions_apres_erreur():
    resultat = construire_tests_explicites(TRANSFORMATION, json.dumps(cas()), 'stock')
    assert 'target.stock = {\'stylo\': 5}' in resultat['code']
    assert 'with pytest.raises(ValueError)' in resultat['code']
    assert 'assert target.stock == {\'stylo\': 5}' in resultat['code']
    assert resultat['origin'] == 'explicit_cases' and not resultat['executed']


@pytest.mark.parametrize('changement', ['membre', 'fonction', 'exception', 'ambigu', 'nan', 'module'])
def test_cas_invalides_ou_injections_ne_produisent_pas_de_code(changement):
    donnees, module = cas(), 'stock'
    if changement == 'membre':
        donnees[0]['setup'] = {'__import__("os")': {}}
    elif changement == 'fonction':
        donnees[0]['function'] = 'retirer(); print(1)'
    elif changement == 'exception':
        donnees[1]['raises'] = 'ExceptionInventee'
    elif changement == 'ambigu':
        donnees[0]['raises'] = 'ValueError'
    elif changement == 'nan':
        donnees[0]['args'] = [float('nan')]
    else:
        module = 'stock; print(1)'
    with pytest.raises(ValueError):
        construire_tests_explicites(TRANSFORMATION, json.dumps(donnees), module)
