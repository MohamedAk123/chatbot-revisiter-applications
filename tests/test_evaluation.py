from revisiter.evaluation import evaluer_cas, rapport
from revisiter.jeux_evaluation import jeu_reference


def test_jeu_trente_questions_et_cas_statiques_reussis_sans_llm():
    cas = jeu_reference()
    assert len(cas) == 30 and len({c['id'] for c in cas}) == 30
    resultats = [evaluer_cas(c) for c in cas]
    assert all(r['status'] == 'reussi' for r in resultats[:15]), [(r['id'], r['status']) for r in resultats[:15]]
    assert all(r['status'] == 'non_execute' for r in resultats[15:])
    bilan = rapport(resultats)
    assert bilan['executed'] == 15 and bilan['checks_passed'] == 15
