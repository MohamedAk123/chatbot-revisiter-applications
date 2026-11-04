import pytest
from test_non_regression import Modele

from revisiter.non_regression import proposer_tests


def test_cas_d_exception_demande_ne_peut_pas_etre_remplace_par_un_assert_ordinaire():
    code = 'from panier import total\ndef test_normal():\n    assert total([2, 3]) == 5\ndef test_limite():\n    assert total([-1]) == -1\n'
    with pytest.raises(ValueError, match='pytest.raises'):
        proposer_tests(Modele(code), {'file': 'panier.py', 'code': 'def total(prix):\n    return sum(prix)'}, 'Valeurs négatives : ValueError attendu')


@pytest.mark.parametrize('code,message', [
    ('import panier as target\ndef test_a():\n    assert target.total([])==0\ndef test_b():\n    with pytest.raises(ValueError):\n        target.total([-1])', 'Nom non défini'),
    ('import panier as target\ndef test_a():\n    assert target.total([])==0\ndef test_b():\n    assert target.reinitialiser()==0', 'Membre du module cible absent'),
])
def test_imports_manquants_et_fonctions_cibles_inventees_rejetes(code, message):
    with pytest.raises(ValueError, match=message):
        proposer_tests(Modele(code), {'file': 'panier.py', 'code': 'def total(prix):\n    return sum(prix)'}, 'Liste vide : zéro')
