"""Interface Flask locale de Revisiter, adossée au moteur d’analyse existant."""

import json
import os
import re
import secrets
from functools import lru_cache

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, render_template, request, session
from httpx import HTTPError
from markdown_it import MarkdownIt
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

from revisiter.config import load_settings
from revisiter.encoders import TransformerEmbeddings
from revisiter.executions import TERMINES, Executions
from revisiter.export import generer_compte_rendu
from revisiter.lecteurs import preparer_document, preparer_extraits
from revisiter.memoire_conversation import resoudre_suivi
from revisiter.ollama_client import create_chat_model
from revisiter.persistance import instantane, valider_instantane
from revisiter.projets import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    FichierProjet,
    fichiers_importes,
    lire_github,
    lire_zip,
)
from revisiter.schemas import choisir_format, code_mermaid
from revisiter.securite_analyse import proteger_documents
from revisiter.stockage import AccesRefuse, ConflitVersion, Stockage
from revisiter.structures import analyser_structure
from revisiter.vocal import (
    MAX_AUDIO_BYTES,
    MAX_QUESTION_CHARS,
    transcription_disponible,
    transcrire_audio,
)
from revisiter.workflow import build_question_graph, iterer_reponse

MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable(["image", "link", "autolink"])
DEMO = {
    "README.md": "# Atlas\nExemple pédagogique de calcul de panier.\n",
    "app/panier.py": "def total_panier(prix):\n    return sum(prix)\n",
    "app/service.py": (
        "from .panier import total_panier as total\n\n"
        "def preparer_commande(prix):\n    return total(prix)\n"
    ),
    "app/interface.py": (
        "from .service import preparer_commande\n\n"
        "def afficher(prix):\n    return preparer_commande(prix)\n"
    ),
}
MODELE_LOCAL = "qwen2.5-coder:3b"


def _objet():
    donnees = request.get_json(silent=True)
    if not isinstance(donnees, dict):
        raise BadRequest("Un objet JSON est attendu.")
    return donnees


def _champ(donnees, nom, limite, *, defaut=""):
    valeur = donnees.get(nom, defaut)
    if not isinstance(valeur, str) or len(valeur) > limite:
        raise BadRequest(f"Champ {nom} invalide (maximum {limite} caractères).")
    return valeur.strip()


def _message(message):
    resultat = {k: message[k] for k in ("role", "content", "sources", "metrics", "assessment", "schema")
                if k in message}
    if message["role"] == "assistant":
        resultat["html"] = MARKDOWN.render(message["content"])
        if message.get("schema"):
            resultat["mermaid"] = code_mermaid(message["schema"])
    return resultat


def _evenement(type_evenement, **donnees):
    return json.dumps({"type": type_evenement, **donnees}, ensure_ascii=False) + "\n"


def create_app(config=None):
    load_dotenv(".env", override=False)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("REVISITER_WEB_SECRET") or secrets.token_bytes(32),
        SESSION_COOKIE_NAME="revisiter_web",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        MAX_CONTENT_LENGTH=MAX_ARCHIVE_BYTES + 1_000_000,
        MAX_FORM_PARTS=5002,
        REVISITER_DATA_DIR=os.getenv("REVISITER_DATA_DIR", ".revisiter"),
    )
    if config:
        app.config.update(config)
    stockage = Stockage(app.config["REVISITER_DATA_DIR"])
    executions = Executions()
    app.extensions["revisiter_executions"] = executions

    @lru_cache(maxsize=3)
    def preparer_graphe(proprietaire, identifiant, fichiers, configuration, recherche):
        documents = dict(fichiers)
        structures = {nom: analyser_structure(nom, contenu) for nom, contenu in fichiers}
        extraits = [p for nom, texte in fichiers for p in preparer_extraits(nom, texte)]
        graphe = build_question_graph(
            extraits, TransformerEmbeddings(configuration.encoder),
            create_chat_model(configuration), top_k=4, retrieval_mode=recherche,
            documents=documents, structures=structures,
        )
        return graphe, structures

    @app.before_request
    def identifier():
        if request.endpoint == "static":
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            attendu = session.get("csrf")
            recu = request.headers.get("X-CSRF-Token", "")
            if not attendu or not recu.isascii() or not secrets.compare_digest(attendu, recu):
                raise Forbidden("Rechargez la page avant de réessayer.")
        g.token = session.get("token")
        try:
            g.utilisateur = stockage.utilisateur(g.token)
        except AccesRefuse:
            g.utilisateur = None
            if request.path.startswith("/api/") and request.endpoint != "connexion":
                return jsonify(error="Connectez-vous pour continuer."), 401

    @app.after_request
    def en_tetes(reponse):
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        reponse.headers["Referrer-Policy"] = "no-referrer"
        reponse.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.endpoint != "static":
            reponse.headers["Cache-Control"] = "no-store"
        return reponse

    @app.errorhandler(ValueError)
    def erreur_donnees(erreur):
        code = 403 if isinstance(erreur, AccesRefuse) else 409 if isinstance(erreur, ConflitVersion) else 400
        return jsonify(error=str(erreur)), code

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    def erreur_http(erreur):
        return jsonify(error=erreur.description), erreur.code

    @app.get("/")
    def accueil():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        session.setdefault("proprietaire", secrets.token_urlsafe(32))
        return render_template(
            "chat.html", csrf=session["csrf"], utilisateur=g.utilisateur,
            partage=stockage.partage, modele=os.getenv("OLLAMA_MODEL") or MODELE_LOCAL,
            mode=os.getenv("OLLAMA_MODE") or "local", vocal=transcription_disponible(),
        )

    @app.post("/api/login")
    def connexion():
        if not stockage.partage:
            raise NotFound()
        donnees = _objet()
        token = stockage.connexion_utilisateur(
            _champ(donnees, "username", 40), donnees.get("password"),
        )
        stockage.deconnecter(g.token)
        session.clear()
        session["token"] = token
        return jsonify(ok=True)

    @app.post("/api/logout")
    def deconnexion():
        stockage.deconnecter(g.token)
        session.clear()
        return jsonify(ok=True)

    def espace(identifiant, *, ecriture=False):
        projet = stockage.ouvrir_projet(identifiant, g.token)
        if ecriture and not projet["can_edit"]:
            raise Forbidden("Cette conversation est en lecture seule.")
        donnees = valider_instantane(projet["snapshot"])
        documents = proteger_documents(donnees["documents"])["documents"]
        return projet, donnees, documents

    def presentation(projet, donnees, documents):
        return {
            "id": projet["id"], "name": projet["name"], "can_edit": projet["can_edit"],
            "files": sorted(documents), "messages": [
                _message(m) for m in donnees["state"].get("messages", [])
            ],
        }

    @app.get("/api/conversations")
    def lister():
        return jsonify([{"id": p["id"], "name": p["name"], "updated": p["updated"]}
                        for p in stockage.lister_projets(g.token)])

    @app.post("/api/conversations")
    def creer():
        donnees = _objet()
        demo = donnees.get("demo") is True
        nom = _champ(donnees, "name", 120) or ("Atlas · Démo" if demo else "Nouvelle conversation")
        documents = DEMO if demo else {}
        identifiant, _ = stockage.enregistrer_projet(nom, instantane(documents, {}), token=g.token)
        projet, donnees, documents = espace(identifiant)
        return jsonify(presentation(projet, donnees, documents)), 201

    @app.get("/api/conversations/<uuid:identifiant>")
    def ouvrir(identifiant):
        return jsonify(presentation(*espace(str(identifiant))))

    @app.patch("/api/conversations/<uuid:identifiant>")
    def renommer(identifiant):
        projet, _, _ = espace(str(identifiant), ecriture=True)
        nom = _champ(_objet(), "name", 120)
        stockage.enregistrer_projet(nom, projet["snapshot"], projet=projet["id"],
                                     version=projet["version"], token=g.token)
        return jsonify(name=nom)

    @app.post("/api/conversations/<uuid:identifiant>/files")
    def importer(identifiant):
        projet, _, documents = espace(str(identifiant), ecriture=True)
        if request.is_json:
            corps = _objet()
            try:
                importation = lire_github(_champ(corps, "url", 400), _champ(corps, "ref", 160) or "HEAD")
            except HTTPError:
                raise BadRequest("GitHub est inaccessible. Réessayez ou importez une archive ZIP.") from None
        else:
            uploads = request.files.getlist("files")
            if len(uploads) == 1 and (uploads[0].filename or "").lower().endswith(".zip"):
                importation = lire_zip(uploads[0].stream.read(MAX_ARCHIVE_BYTES + 1))
            else:
                importation = fichiers_importes([
                    FichierProjet(f.filename or "", f.stream.read(MAX_FILE_BYTES + 1)) for f in uploads
                ])
        protection = proteger_documents({f.name: f.data.decode("utf-8-sig") for f in importation.fichiers})
        ajouts = protection["documents"]
        if not ajouts:
            raise BadRequest("Aucun fichier de code exploitable. Vérifiez les formats et le contenu.")
        connus = {nom.casefold(): nom for nom in documents}
        for nom, texte in ajouts.items():
            if nom.lower().endswith(".ipynb"):
                preparer_document(nom, texte)
            ancien = connus.get(nom.casefold())
            if ancien is not None and (ancien != nom or documents[ancien] != texte):
                raise Conflict("Un fichier a changé. Importez cette version dans une nouvelle conversation.")
        documents.update(ajouts)
        snapshot = {**projet["snapshot"], "documents": documents}
        valider_instantane(snapshot)
        stockage.enregistrer_projet(projet["name"], snapshot, projet=projet["id"],
                                     version=projet["version"], token=g.token)
        secrets_masques = sum(f["kind"] != "instruction suspecte à examiner" for f in protection["findings"])
        return jsonify(files=sorted(documents), ignored=len(importation.ignores) + len(protection["excluded"]),
                       secrets=secrets_masques)

    @app.get("/api/conversations/<uuid:identifiant>/file")
    def fichier(identifiant):
        _, _, documents = espace(str(identifiant))
        nom = request.args.get("name", "")
        if nom in documents:
            return jsonify(name=nom, code=preparer_document(nom, documents[nom]))
        reference = re.fullmatch(r"(.+):L([1-9]\d{0,6})(?:-L([1-9]\d{0,6}))?", nom)
        if reference:
            source, debut, fin = reference[1], int(reference[2]), int(reference[3] or reference[2])
            if debut <= fin and fin - debut <= 500 and source in documents and not source.lower().endswith(".ipynb"):
                lignes = documents[source].splitlines()
                if fin <= len(lignes):
                    premier, dernier = max(1, debut - 3), min(len(lignes), fin + 3)
                    code = "\n".join(f"{i:>4}  {lignes[i - 1]}" for i in range(premier, dernier + 1))
                    return jsonify(name=nom, code=code)
            parent = source.split("#", 1)[0]
            if debut <= fin and parent in documents and parent.lower().endswith(".ipynb"):
                passages = [c for c in preparer_extraits(parent, documents[parent]) if c.source == source
                            and c.start_line <= fin and c.end_line >= debut]
                if passages:
                    return jsonify(name=nom, code="\n\n".join(c.text for c in passages))
        raise NotFound("Ce fichier ou passage ne fait pas partie de la conversation.")

    @app.get("/api/conversations/<uuid:identifiant>/export")
    def exporter(identifiant):
        projet, donnees, documents = espace(str(identifiant))
        texte = generer_compte_rendu(projet["name"], sorted(documents), donnees["state"].get("messages", []))
        return Response(texte, mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=revisiter-discussion.md"})

    @app.post("/api/conversations/<uuid:identifiant>/messages")
    def repondre(identifiant):
        projet, donnees, documents = espace(str(identifiant), ecriture=True)
        corps = _objet()
        question = _champ(corps, "question", MAX_QUESTION_CHARS)
        if not question:
            raise BadRequest("Écrivez une question.")
        format_reponse = _champ(corps, "format", 10, defaut="Auto")
        recherche = _champ(corps, "search", 10, defaut="auto")
        if format_reponse not in {"Auto", "Texte", "Schéma"} or recherche not in {"auto", "lexical", "semantic"}:
            raise BadRequest("Le mode de réponse ou de recherche est invalide.")
        historique = donnees["state"].get("messages", [])
        proprietaire, token = session["proprietaire"], g.token
        try:
            configuration = load_settings(mode_par_defaut="local", modele_par_defaut=MODELE_LOCAL)
        except ValueError as erreur:
            raise BadRequest(str(erreur)) from None

        def travail(controle, emettre):
            controle.phase = "préparation du projet"
            graphe, structures = preparer_graphe(
                proprietaire, projet["id"], tuple(sorted(documents.items())), configuration, recherche,
            )
            resolution = resoudre_suivi(question, historique, documents, structures)
            if resolution["clarification"]:
                return {"answer": resolution["clarification"], "sources": []}
            entree = {
                "question": resolution["question"], "history": historique[-12:],
                "project_name": projet["name"], "controle": controle,
                "response_format": choisir_format(question, format_reponse),
            }
            resultat = {}
            for morceau in iterer_reponse(graphe, entree, resultat):
                emettre(morceau)
            return resultat

        travail_id = executions.lancer(proprietaire, projet["id"], question, travail)

        def flux():
            offset, phase = 0, None
            try:
                yield _evenement("start", id=travail_id)
                while True:
                    etat = executions.attendre(travail_id, proprietaire, 0.17)
                    if etat["status"] in TERMINES:
                        break
                    if etat["phase"] != phase:
                        phase = etat["phase"]
                        yield _evenement("state", phase=phase)
                    if len(etat["text"]) > offset:
                        yield _evenement("chunk", text=etat["text"][offset:])
                        offset = len(etat["text"])
                if etat["status"] != "terminé":
                    yield _evenement("error", error=etat["error"] or "Génération arrêtée.")
                    return
                resultat = etat["result"]
                messages = [{"role": "user", "content": question}, {
                    "role": "assistant", "content": resultat["answer"],
                    "sources": resultat.get("sources", []), "context": resultat.get("context", ""),
                    "metrics": {**resultat.get("metrics", {}), "total_ms": etat["elapsed_ms"]},
                    "assessment": resultat.get("assessment"), "schema": resultat.get("schema"),
                }]
                snapshot = {**projet["snapshot"], "documents": documents, "state": {
                    **donnees["state"], "messages": [*historique, *messages],
                }}
                nom = (question.splitlines()[0][:64] if projet["name"] == "Nouvelle conversation"
                       else projet["name"])
                stockage.enregistrer_projet(nom, snapshot, projet=projet["id"],
                                             version=projet["version"], token=token)
                yield _evenement("complete", messages=[_message(m) for m in messages], name=nom)
            except ValueError as erreur:
                yield _evenement("error", error=str(erreur))
            finally:
                executions.annuler(travail_id, proprietaire)
                if executions.lire(travail_id, proprietaire)["status"] in TERMINES:
                    executions.oublier(travail_id, proprietaire)

        return Response(flux(), mimetype="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    @app.delete("/api/executions/<uuid:identifiant>")
    def annuler(identifiant):
        try:
            executions.annuler(str(identifiant), session["proprietaire"])
        except PermissionError:
            raise NotFound("Cette génération n’est plus disponible.") from None
        return "", 204

    @app.post("/api/transcription")
    def transcrire():
        audio = request.files.get("audio")
        if audio is None:
            raise BadRequest("Enregistrez une question avec le microphone.")
        langue = request.form.get("langue", "fr") or None
        return jsonify(text=transcrire_audio(audio.stream.read(MAX_AUDIO_BYTES + 1), langue))

    return app
