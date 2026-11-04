import pytest

from revisiter.memoire_conversation import resoudre_suivi
from revisiter.preuves import examiner_reponse

DOCS = {'panier.py': 'def total_panier(prix):\n    return sum(prix)\n'}


def test_calcul_faux_contredit_et_calcul_juste_confirme_sans_execution():
    for nombre, attendu in [(7, False), (10, True)]:
        texte = f'`total_panier([10, -3])` donne **{nombre}** [panier.py:L1-L2].'
        examen = examiner_reponse(texte, ['panier.py:L1-L2'], DOCS)
        assert examen['contradiction'] is attendu
        assert any(c['kind'] == 'calcul contrôlé' for c in examen['checks'])


def test_appel_syntaxique_et_absence_pas_confondue_avec_faussete():
    docs = {'a.py': 'def total(x):\n    return x\ndef service(x):\n    return total(x)\n'}
    examen = examiner_reponse('`service` appelle `total`. `service` appelle `inconnue`.', [], docs)
    assert [c['status'] for c in examen['checks']] == ['confirmé syntaxiquement', 'non confirmé']
    assert not examen['contradiction']


def test_une_citation_valide_ne_valide_pas_une_affirmation_metier():
    examen = examiner_reponse('Cette fonction est sécurisée [panier.py:L1-L2]. Elle pourrait servir à facturer.',
                              ['panier.py:L1-L2'], DOCS)
    assert examen['scope'] == 'Réponse générée à relire'
    assert examen['checks'][0]['kind'] == 'plage de citation' and examen['hypotheses']
    assert not examen['contradiction']


def test_suivi_reprend_le_symbole_et_les_contraintes_de_l_utilisateur():
    histoire = [{'role': 'user', 'content': 'Explique total_panier dans panier.py sans modifier son API.'}]
    suivi = resoudre_suivi('Et les tests ?', histoire, DOCS)
    assert suivi['subject'] == 'panier.py · total_panier' and not suivi['clarification']
    assert 'sans modifier son API' in suivi['question']


def test_suivi_ambigu_demande_une_precision_et_n_invente_pas_un_fichier():
    docs = {**DOCS, 'autre.py': 'def total_panier(x):\n    return x\n'}
    histoire = [{'role': 'user', 'content': 'Explique total_panier.'}]
    assert resoudre_suivi('Corrige cette fonction.', histoire, docs)['clarification']
    assert resoudre_suivi('Corrige cette fonction.', histoire, {})['clarification']
    assert resoudre_suivi('Quel est le rôle des fichiers ?', histoire, docs)['question'] == 'Quel est le rôle des fichiers ?'


def test_graphe_refuse_un_calcul_genere_faux_meme_avec_une_citation_valide():
    from langchain_core.messages import AIMessage
    from test_rapidite import SansEncodeur

    from revisiter.lecteurs import preparer_extraits
    from revisiter.robustesse import ErreurReponse
    from revisiter.workflow import build_question_graph

    class Modele:
        def invoke(self, messages):
            return AIMessage(content='`total_panier([10, -3])` donne **10** [panier.py:L1-L2].')

    graphe = build_question_graph(preparer_extraits('panier.py', DOCS['panier.py']), SansEncodeur(), Modele(),
                                 retrieval_mode='semantic', documents=DOCS)
    with pytest.raises(ErreurReponse, match='contredit'):
        graphe.invoke({'question': 'Quel résultat ?', 'request_started': 0})
