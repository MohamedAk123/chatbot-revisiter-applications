import io
import json
import re
import time

import pytest

from revisiter import web
from revisiter.stockage import Stockage


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODE", "local")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    serveur = web.create_app({"TESTING": True, "REVISITER_DATA_DIR": tmp_path})
    yield serveur
    serveur.extensions["revisiter_executions"].fermer()


def page(client):
    accueil = client.get("/")
    assert accueil.status_code == 200
    token = re.search(r'name="csrf-token" content="([^"]+)"', accueil.text)
    return {"X-CSRF-Token": token[1]}


def creer(client, csrf, *, demo=False):
    reponse = client.post("/api/conversations", json={"demo": demo}, headers=csrf)
    assert reponse.status_code == 201
    return "/api/conversations/" + reponse.json["id"]


def test_atlas_reponse_et_source_persistantes(app, monkeypatch):
    monkeypatch.setattr(web, "create_chat_model", lambda _config: object())
    client = app.test_client()
    url = creer(client, page(client), demo=True)
    reponse = client.post(url + "/messages", json={
        "question": "Quel est le résultat de total_panier([10, -3]) ?",
    }, headers=page(client), buffered=True)
    events = [json.loads(ligne) for ligne in reponse.text.splitlines()]
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "complete"
    historique = app.test_client().get(url).json["messages"]
    assert len(historique) == 2
    assert "<strong>7</strong>" in historique[1]["html"]
    reference = historique[1]["sources"][0]
    assert "return sum(prix)" in client.get(url + "/file", query_string={"name": reference}).json["code"]
    assert "total_panier" in client.get(url + "/export").text


def test_import_masque_secret_et_protege_version(app):
    client = app.test_client()
    csrf = page(client)
    url = creer(client, csrf)
    fichier = (io.BytesIO(b'API_KEY = "cle-fictive-exemple"\nx = 1\n'), "config.py")
    assert client.post(url + "/files", data={"files": fichier}, headers=csrf).status_code == 200
    contenu = client.get(url + "/file?name=config.py").json["code"]
    assert "cle-fictive-exemple" not in contenu and "[SECRET_MASQUE]" in contenu
    nouveau = (io.BytesIO(b"x = 2\n"), "config.py")
    assert client.post(url + "/files", data={"files": nouveau}, headers=csrf).status_code == 409
    assert client.post("/api/conversations", json={}).status_code == 403


def test_annulation_ne_conserve_pas_la_reponse_partielle(app, monkeypatch):
    def attente(graphe, entree, resultat):
        yield "Fragment temporaire."
        while True:
            entree["controle"].verifier()
            time.sleep(0.02)

    monkeypatch.setattr(web, "iterer_reponse", attente)
    client = app.test_client()
    csrf = page(client)
    url = creer(client, csrf, demo=True)
    reponse = client.post(url + "/messages", json={"question": "Explique le projet."}, headers=csrf)
    identifiant = json.loads(next(iter(reponse.response)))["id"]
    autre = app.test_client()
    assert autre.delete("/api/executions/" + identifiant, headers=page(autre)).status_code == 404
    assert client.delete("/api/executions/" + identifiant, headers=csrf).status_code == 204
    assert json.loads(list(reponse.response)[-1])["type"] == "error"
    assert client.get(url).json["messages"] == []


def test_conversations_respectent_les_comptes(tmp_path, monkeypatch):
    monkeypatch.setenv("REVISITER_AUTH_ENABLED", "true")
    stockage = Stockage(tmp_path)
    stockage.initialiser_admin("alice", "mot-de-passe-fictif-alice")
    alice = stockage.connexion_utilisateur("alice", "mot-de-passe-fictif-alice")
    stockage.creer_utilisateur(alice, "bob", "mot-de-passe-fictif-pour-bob")
    serveur = web.create_app({"TESTING": True, "REVISITER_DATA_DIR": tmp_path})
    try:
        client = serveur.test_client()
        assert client.get("/api/conversations").status_code == 401
        for username, password in (("alice", "mot-de-passe-fictif-alice"),
                                   ("bob", "mot-de-passe-fictif-pour-bob")):
            assert client.post("/api/login", headers=page(client), json={
                "username": username, "password": password,
            }).status_code == 200
            if username == "alice":
                url = creer(client, page(client), demo=True)
        assert client.get(url).status_code == 403
        assert client.get("/api/conversations").json == []
    finally:
        serveur.extensions["revisiter_executions"].fermer()
