import pytest
from langchain_core.messages import AIMessageChunk

from revisiter.non_regression import proposer_tests


class Modele:
    def __init__(self, code):
        self.code = code

    def stream(self, messages):
        yield AIMessageChunk(content=self.code)


def test_generation_sans_execution_et_empreinte_du_code_cible():
    code = 'from panier import total\ndef test_normal():\n    assert total([2, 3]) == 5\ndef test_vide():\n    assert total([]) == 0\n'
    resultat = proposer_tests(Modele(code), {'file': 'src/panier.py', 'code': 'def total(prix):\n    return sum(prix)'}, '2+3=5 ; liste vide=0')
    assert resultat['syntax'] == 'ok' and not resultat['executed']
    assert resultat['file'] == 'test_panier.py' and len(resultat['target_sha256']) == 64


@pytest.mark.parametrize('code', ['def test_a(:', 'def test_a():\n    assert True\ndef test_b():\n    assert True',
    'def total(prix):\n    return 0\ndef test_a():\n    assert total([])==0\ndef test_b():\n    assert total([0])==0'])
def test_tests_invalides_triviaux_ou_copie_du_code_rejetes(code):
    with pytest.raises(ValueError):
        proposer_tests(Modele(code), {'file': 'panier.py', 'code': 'def total(prix):\n    return sum(prix)'}, 'liste vide = 0')
