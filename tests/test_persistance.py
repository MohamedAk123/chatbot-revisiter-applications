
import pytest
from test_guide import recommandation
from test_recherche import CountingEmbeddings

from revisiter.guide import build_guided_plan_graph, reprendre_guide
from revisiter.index_incremental import EmbeddingsPersistants, IndexIncremental
from revisiter.ingestion import Chunk
from revisiter.persistance import instantane, restaurer_guide, valider_instantane
from revisiter.stockage import Stockage


def test_index_persistant_ne_recalcule_que_le_fichier_modifie(tmp_path):
    s = Stockage(tmp_path, partage=False)
    projet, _ = s.enregistrer_projet('Atlas', {})
    premier = IndexIncremental({}, s, projet)
    premier.preparer('a.py', 'a=1')
    premier.preparer('b.py', 'b=1')
    second = IndexIncremental({}, s, projet)
    second.preparer('a.py', 'a=1')
    second.preparer('b.py', 'b=2')
    assert premier.recalcules == 2 and second.reutilises == 1 and second.recalcules == 1


def test_vecteurs_reutilises_apres_recreation_encodeur_sans_charger_nlp(tmp_path):
    s = Stockage(tmp_path, partage=False)
    projet, _ = s.enregistrer_projet('Atlas', {})
    base = CountingEmbeddings()
    a = EmbeddingsPersistants(base, s, projet, None, 'camembert')
    a.embed_documents(['a=1', 'b=1'])
    b = EmbeddingsPersistants(base, s, projet, None, 'camembert')
    b.embed_documents(['a=1', 'b=2'])
    assert base.document_calls == [['a=1', 'b=1'], ['b=2']]
    assert b.reutilises == b.recalcules == 1


def test_instantane_exclut_objets_et_secrets_et_reprend_guide_actif():
    chunks = [Chunk('demo.py', 1, 2, 'a=1\nb=2')]
    graphe = build_guided_plan_graph(chunks, recommandation())
    config = {'configurable': {'thread_id': 'initial'}}
    graphe.invoke({}, config)
    reprendre_guide(graphe, config, {'action': 'confirmer', 'observation': 'Copie comparée.'})
    session = {'messages': [{'role': 'user', 'content': 'Question'}], 'api_key': 'SECRET',
        'encodeur': object(), 'guide': {'graphe': graphe, 'config': config}}
    sauvegarde = instantane({'demo.py': 'a=1\nb=2'}, session)
    assert 'SECRET' not in str(sauvegarde) and 'encodeur' not in sauvegarde['state']
    copie = valider_instantane(sauvegarde)
    nouveau = restaurer_guide(copie['guide'], chunks)
    etat = nouveau['graphe'].get_state(nouveau['config']).values
    assert etat['position'] == 1 and etat['observations'][0] == 'Copie comparée.'
    suite = reprendre_guide(nouveau['graphe'], nouveau['config'], {'action': 'confirmer'})
    assert suite['position'] == 2


def test_instantane_ne_restaure_pas_des_cles_d_authentification():
    contenu = {'format': 1, 'documents': {}, 'state': {'auth_token': 'INJECTION', 'messages': []}}
    assert 'auth_token' not in valider_instantane(contenu)['state']
    contenu['documents'] = {'../autre.py': 'a=1'}
    with pytest.raises(ValueError):
        valider_instantane(contenu)
