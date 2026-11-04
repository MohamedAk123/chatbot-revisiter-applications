"""Le son n'est jamais envoyé au graphe avant validation du texte reconnu."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from streamlit.testing.v1 import AppTest

from revisiter import vocal_ui
from revisiter.vocal import ErreurVocale

PAGE = '''
import streamlit as st
from revisiter.vocal_ui import saisie_question
q = saisie_question(st.session_state.get('contexte_test', 'compte1:atlas'))
if q:
    st.session_state['envoyees'] = st.session_state.get('envoyees', []) + [q]
'''


def simuler_audio(monkeypatch, texte='Quel est le rôle de ce fichier ?', erreur=None):
    appels = []
    valeurs = [SimpleNamespace(text='', audio=BytesIO(b'audio-de-test'))]
    monkeypatch.setattr(st, 'chat_input', lambda *a, **kw: valeurs.pop(0) if valeurs else None)
    monkeypatch.setattr(vocal_ui, 'transcription_disponible', lambda: True)

    def transcrire(audio, langue):
        appels.append((audio, langue))
        if erreur:
            raise erreur
        return texte

    monkeypatch.setattr(vocal_ui, 'transcrire_audio', transcrire)
    return appels


def test_audio_transcrit_relecture_correction_et_un_seul_envoi(monkeypatch):
    appels = simuler_audio(monkeypatch)
    app = AppTest.from_string(PAGE).run(timeout=20)
    assert not app.exception and app.session_state.get('envoyees', []) == []
    assert app.text_area(key='vocal_texte').value == 'Quel est le rôle de ce fichier ?'
    app.text_area(key='vocal_texte').set_value('Explique total_panier dans app/panier.py.')
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert app.session_state['envoyees'] == ['Explique total_panier dans app/panier.py.']
    app.run(timeout=20)
    assert len(app.session_state['envoyees']) == 1 and len(appels) == 1


def test_annuler_ne_transmet_aucune_question(monkeypatch):
    simuler_audio(monkeypatch)
    app = AppTest.from_string(PAGE).run(timeout=20)
    app.button[1].click().run(timeout=20)
    assert not app.exception and not app.text_area
    assert app.session_state.get('envoyees', []) == []


def test_brouillon_vide_ne_part_pas(monkeypatch):
    simuler_audio(monkeypatch)
    app = AppTest.from_string(PAGE).run(timeout=20)
    app.text_area(key='vocal_texte').set_value('   ')
    app.button[0].click().run(timeout=20)
    assert not app.exception and app.warning
    assert app.session_state.get('envoyees', []) == []


def test_changer_de_projet_supprime_le_brouillon(monkeypatch):
    simuler_audio(monkeypatch)
    app = AppTest.from_string(PAGE).run(timeout=20)
    app.session_state['contexte_test'] = 'compte2:autre-projet'
    app.run(timeout=20)
    assert not app.exception and not app.text_area
    assert app.session_state.get('envoyees', []) == []


def test_echec_vocal_ne_bloque_pas_le_texte(monkeypatch):
    simuler_audio(monkeypatch, erreur=ErreurVocale('Aucune parole reconnue.'))
    app = AppTest.from_string(PAGE).run(timeout=20)
    assert not app.exception and app.error[0].value == 'Aucune parole reconnue.'
    assert not app.text_area and app.session_state.get('envoyees', []) == []
    monkeypatch.setattr(st, 'chat_input', lambda *a, **kw: 'Explique ce code.')
    app.run(timeout=20)
    assert not app.exception and app.session_state['envoyees'] == ['Explique ce code.']


def test_saisie_texte_native_fonctionne_sans_dependances_vocales(monkeypatch):
    monkeypatch.setattr(vocal_ui, 'transcription_disponible', lambda: False)
    app = AppTest.from_string(PAGE).run(timeout=20)
    assert not app.exception and app.info
    app.chat_input[0].set_value('Quel est le rôle de ce fichier ?').run(timeout=20)
    assert not app.exception and app.session_state['envoyees'] == ['Quel est le rôle de ce fichier ?']


def test_envoi_audio_utilise_le_meme_graphe_que_le_texte(monkeypatch):
    from test_rapidite import SansEncodeur
    from test_recherche import RecordingModel, Upload

    from revisiter import config, encoders, ollama_client
    from revisiter.config import Settings

    modele = RecordingModel()
    simuler_audio(monkeypatch, texte='Quel est le résultat de total_panier([10, -3]) ?')
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **kw: [Upload('panier.py', b'def total_panier(prix):\n    return sum(prix)\n')])
    monkeypatch.setattr(config, 'load_settings', lambda: Settings('local', 'modele-test'))
    monkeypatch.setattr(encoders, 'TransformerEmbeddings', lambda *a: SansEncodeur())
    monkeypatch.setattr(ollama_client, 'create_chat_model', lambda *a: modele)
    app = AppTest.from_file(str(Path(__file__).parents[1] / 'app.py')).run(timeout=20)
    assert not app.exception and app.session_state['messages'] == []
    app.button(key='FormSubmitter:confirmation_vocale-Envoyer la question').click().run(timeout=20)
    assert not app.exception and not app.error
    assert len(app.session_state['messages']) == 2
    reponse = app.session_state['messages'][1]
    assert '**7**' in reponse['content'] and reponse['sources'] == ['panier.py:L1-L2']
    assert not modele.calls
