"""Régression Atlas : schéma direct, alias, sources, limites et maintien du LLM."""

from copy import deepcopy
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_rapidite import SansEncodeur
from test_recherche import RecordingModel, Upload
from test_roles_fichiers import ATLAS
from test_schemas import proposition

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.lecteurs import preparer_extraits
from revisiter.relations import construire_relations
from revisiter.robustesse import controler_citations
from revisiter.schemas import ErreurSchema, code_mermaid
from revisiter.schemas_appels import schema_appels
from revisiter.workflow import build_question_graph, iterer_reponse

QUESTION = ('Fais un schéma des appels entre afficher, preparer_commande et total_panier. '
            'Explique chaque étape et cite les lignes correspondantes.')


def graphe(documents, modele):
    chunks = [e for n, t in documents.items() for e in preparer_extraits(n, t)]
    return build_question_graph(chunks, SansEncodeur(), modele, retrieval_mode='auto', documents=documents)


def test_schema_atlas_direct_sans_modele_ni_nlp_et_avec_preuves_des_alias():
    class SansModele:
        def with_structured_output(self, *args, **kwargs):
            raise AssertionError('Le schéma d’appels doit être construit sans Ollama.')

    resultat = {}
    texte = ''.join(iterer_reponse(graphe(ATLAS, SansModele()), {
        'question': QUESTION, 'response_format': 'schema'}, resultat))
    schema = resultat['schema']
    assert [n['libelle'] for n in schema['noeuds']] == ['afficher', 'preparer_commande', 'total_panier']
    assert [(l['de'], l['vers'], l['libelle']) for l in schema['liens']] == [(1, 2, 'appelle'), (2, 3, 'appelle')]
    assert schema['liens'][1]['references'] == ['app/service.py:L4-L4', 'app/service.py:L1-L1']
    assert controler_citations(texte, resultat['sources']) == 'checked'
    assert all('[' + r + ']' in resultat['context'] for r in resultat['sources'])
    assert resultat['metrics']['method'] == 'static' and resultat['metrics']['generation_ms'] == 0
    mermaid = code_mermaid(schema)
    assert '"appelle"' in mermaid and 'dans la' not in mermaid
    assert 'sum' not in mermaid  # La fonction externe n'est pas une déclaration locale.


def test_fonction_unique_et_import_js_suivent_les_liens_resolus():
    documents = {'a.ts': 'export function total(x: number) { return x; }',
                 'b.ts': 'import { total as calcul } from "./a";\nexport function afficher(x: number) { return calcul(x); }'}
    resultat = schema_appels('Schéma des appels de afficher', construire_relations(documents), documents)
    assert [n['libelle'] for n in resultat['direct_schema']['noeuds']] == ['afficher', 'total']
    assert resultat['direct_schema']['liens'][0]['references'] == ['b.ts:L2-L2', 'b.ts:L1-L1']


def test_homonymes_demandent_un_fichier_et_nom_inconnu_est_signale():
    documents = {'a.py': 'def f():\n    return 1', 'b.py': 'def f():\n    return 2'}
    relations = construire_relations(documents)
    with pytest.raises(ErreurSchema, match='Plusieurs fonctions'):
        schema_appels('Schéma des appels de f', relations, documents)
    resultat = schema_appels('Schéma des appels de f dans `a.py`', relations, documents)
    assert resultat['sources'] == ['a.py:L1-L1']
    for question in ['Schéma des appels entre afficher et inconnue',
                     'Schéma des appels de afficher et `inconnue`']:
        with pytest.raises(ErreurSchema, match='inconnue'):
            schema_appels(question, construire_relations(ATLAS), ATLAS)


def test_liaisons_possibles_non_transformees_en_appels_certains():
    documents = {'a.py': 'def afficher(obj):\n    obj.total()\ndef total():\n    return 1'}
    resultat = schema_appels('Schéma des appels de afficher', construire_relations(documents), documents)
    assert [n['libelle'] for n in resultat['direct_schema']['noeuds']] == ['afficher']
    assert not resultat['direct_schema']['liens']
    assert 'Aucun appel local résolu' in resultat['direct_schema']['explication']


def test_cycles_preserves_sans_inventer_un_ordre_et_doublons_evitent_fausses_fleches():
    documents = {'a.py': 'def f():\n    g()\ndef g():\n    f()'}
    resultat = schema_appels('Schéma des appels de f', construire_relations(documents), documents)
    schema = resultat['direct_schema']
    assert {n['libelle'] for n in schema['noeuds']} == {'f', 'g'}
    assert len(schema['liens']) == 2
    assert {(l['de'], l['vers']) for l in schema['liens']} == {(1, 2), (2, 1)}


def test_graphe_borne_et_preuves_trop_longues_refusees_avant_modele():
    documents = {'a.py': 'def racine():\n' + ''.join(f'    f{i}()\n' for i in range(12))
                 + ''.join(f'def f{i}():\n    return {i}\n' for i in range(12))}
    relations = construire_relations(documents)
    resultat = schema_appels('Schéma des appels de racine', relations, documents)
    assert len(resultat['direct_schema']['noeuds']) == 8
    assert 'limite atteinte' in resultat['direct_schema']['explication']
    assert len(resultat['context']) <= 6000
    with pytest.raises(ErreurSchema, match='budget'):
        schema_appels('Schéma des appels de racine', relations, documents, budget=12)
    question = 'Schéma des appels entre ' + ', '.join(f'f{i}' for i in range(9))
    with pytest.raises(ErreurSchema, match='huit fonctions'):
        schema_appels(question, relations, documents)


def test_references_hors_fichier_rejetees():
    relations = deepcopy(construire_relations(ATLAS))
    lien = next(l for l in relations['edges'] if l['kind'] == 'call')
    lien['reference'] = 'app/interface.py:L999-L999'
    with pytest.raises(ErreurSchema, match='dépasse les lignes'):
        schema_appels(QUESTION, relations, ATLAS)


def test_suivi_textuel_ne_reutilise_pas_le_schema_direct():
    modele = RecordingModel()
    g = graphe(ATLAS, modele)
    schema = g.invoke({'question': QUESTION, 'response_format': 'schema'})
    assert not modele.calls
    suivi = g.invoke({'question': 'Explique l’étape 2 du schéma.', 'response_format': 'texte',
                     'history': [{'role': 'assistant', 'content': schema['answer']}]})
    assert suivi['schema'] is None and len(modele.calls) == 1


@pytest.mark.parametrize('question', [
    'Schéma des conditions et appels de total_panier',
    'Fais un schéma de fonctionnement de total_panier.',
    'Fais un diagramme de séquence des appels de total_panier.',
])
def test_interpretations_algorithmes_et_temporalite_conservent_le_modele(question):
    modele = RecordingModel()
    modele.structured_result = proposition()
    resultat = graphe(ATLAS, modele).invoke({'question': question, 'response_format': 'schema'})
    assert len(modele.calls) == 1
    assert resultat['schema']['titre'] == 'Calcul du total'


def test_interface_dessine_le_schema_direct_et_le_conserve_sans_regeneration(monkeypatch):
    class SansModele(RecordingModel):
        def with_structured_output(self, *args, **kwargs):
            raise AssertionError('Appel Ollama inattendu pour le schéma d’appels.')

    modele, dessins = SansModele(), []
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **kw: [Upload(n, t.encode()) for n, t in ATLAS.items()])
    monkeypatch.setattr(config, 'load_settings', lambda: Settings('local', 'modele-test'))
    monkeypatch.setattr(encoders, 'TransformerEmbeddings', lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, 'create_chat_model', lambda settings: modele)
    monkeypatch.setattr(st, 'mermaid_chart', lambda code, **kw: dessins.append(code))
    app = AppTest.from_file(str(Path(__file__).parents[1] / 'app.py')).run(timeout=15)
    app.chat_input[0].set_value(QUESTION).run(timeout=15)
    assert not app.exception and not app.error
    message = deepcopy(app.session_state['messages'][-1])
    assert message['metrics']['method'] == 'static'
    assert len(message['schema']['noeuds']) == 3
    assert code_mermaid(message['schema']) in dessins
    assert not modele.calls
    app.run(timeout=15)
    assert not app.exception and not app.error
    assert app.session_state['messages'][-1] == message
    assert not modele.calls
