from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest
from test_recherche import CountingEmbeddings, RecordingModel, Upload

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.stockage import Stockage


def preparer(monkeypatch, tmp_path, partage=False):
    monkeypatch.setenv('REVISITER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REVISITER_AUTH_ENABLED', 'true' if partage else 'false')
    fichiers = []
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **kw: list(fichiers))
    monkeypatch.setattr(config, 'load_settings', lambda: Settings('local', 'modele-test'))
    monkeypatch.setattr(encoders, 'TransformerEmbeddings', lambda encoder: CountingEmbeddings())
    monkeypatch.setattr(ollama_client, 'create_chat_model', lambda settings, **kw: RecordingModel())
    return AppTest.from_file(str(Path(__file__).parents[1] / 'app.py')), fichiers


def test_sauvegarder_fermer_et_reouvrir_projet_et_discussion(monkeypatch, tmp_path):
    app, fichiers = preparer(monkeypatch, tmp_path)
    fichiers.append(Upload('src/demo.py', b'def f():\n    return 1'))
    app.run(timeout=15)
    app.text_input(key='nom_projet').set_value('Atlas').run(timeout=15)
    app.chat_input[0].set_value('Liste les noms des fonctions.').run(timeout=15)
    assert not app.exception
    app.button(key='enregistrer_espace').click().run(timeout=15)
    identifiant = app.session_state['espace_id']
    assert Stockage(tmp_path, partage=False).ouvrir_projet(identifiant)['snapshot']['state']['messages']
    with Stockage(tmp_path, partage=False).connexion() as db:
        assert db.execute("SELECT COUNT(*) FROM caches WHERE project_id=? AND kind='document'", (identifiant,)).fetchone()[0] == 1
    fichiers.clear()
    app.button(key='nouvel_espace').click().run(timeout=15)
    assert not app.exception and not app.session_state['messages']
    app.button(key='ouvrir_espace').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['nom_projet'] == 'Atlas'
    assert len(app.session_state['messages']) == 2
    assert 'src/demo.py' in app.session_state['documents_restaures']


def test_mode_partage_connexion_et_deconnexion_sans_reveler_espace(monkeypatch, tmp_path):
    stockage = Stockage(tmp_path, partage=True)
    stockage.initialiser_admin('admin', 'mot-test-uniquement-2026')
    app, _ = preparer(monkeypatch, tmp_path, partage=True)
    app.run(timeout=15)
    assert not app.exception and app.text_input(key='login_identifiant')
    app.text_input(key='login_identifiant').set_value('admin')
    app.text_input(key='login_mot').set_value('mot-test-uniquement-2026')
    next(b for b in app.button if b.label == 'Se connecter').click().run(timeout=15)
    assert not app.exception and app.text_input(key='nom_projet')
    token = app.session_state['auth_token']
    assert stockage.utilisateur(token)['username'] == 'admin'
    app.button(key='deconnexion').click().run(timeout=15)
    assert not app.exception and app.text_input(key='login_identifiant')
    assert 'auth_token' not in app.session_state
