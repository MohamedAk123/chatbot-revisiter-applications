"""Interface de discussion avec recherche de passages dans les fichiers importés."""

import hashlib
from html import escape
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import streamlit as st
from httpx import HTTPError, TimeoutException
from ollama import ResponseError

from revisiter.cartographie_ui import afficher_cartographie
from revisiter.config import load_settings
from revisiter.encoders import TransformerEmbeddings
from revisiter.espaces_ui import authentifier, barre_espaces, enregistrer_etat, reprendre_etat
from revisiter.evaluation_ui import afficher_evaluation
from revisiter.executions_ui import (
    afficher_execution,
    afficher_retour_execution,
    annuler_execution,
    lancer_execution,
)
from revisiter.export import generer_compte_rendu
from revisiter.guide import (
    CHAMPS_TEXTE,
    PHASES,
    ErreurGuide,
    build_guided_plan_graph,
    reprendre_guide,
)
from revisiter.index_incremental import EmbeddingsPersistants, IndexIncremental
from revisiter.ingestion import ALLOWED_EXTENSIONS
from revisiter.interface import (
    accueil_discussion,
    appliquer_style,
    entete_action,
    etat_vide,
    formater_reponse,
    indicateurs,
    introduction,
    marque,
    priorite,
    progression_guide,
    sources_message,
)
from revisiter.ollama_client import create_chat_model
from revisiter.projet_ui import selection_import
from revisiter.projets import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PROJECT_BYTES,
    FichierProjet,
    arborescence,
)
from revisiter.qualite_ui import (
    afficher_benchmark_projet,
    afficher_comparaison_versions,
    afficher_diagnostic_requetes,
    afficher_preuves,
    afficher_securite,
    preferences_securite,
)
from revisiter.recommandations import ErreurRecommandations, build_recommendation_graph
from revisiter.robustesse import message_erreur
from revisiter.schema_ui import afficher_schema
from revisiter.schemas import choisir_format
from revisiter.securite_analyse import examiner_fichier
from revisiter.ui_professionnelle import (
    afficher_mesures,
    afficher_transformation,
    controles_performance,
)
from revisiter.vocal_ui import effacer_brouillon, saisie_question
from revisiter.workflow import (
    MAX_CONTEXT_CHARS,
    build_question_graph,
    build_summary_graph,
)

ERREURS_IA = (
    ConnectionError, HTTPError, ResponseError, ValueError,
    RuntimeError, OSError, ImportError, TypeError,
)


def afficher_message(message, cle="courant"):
    avatar = ":material/auto_awesome:" if message["role"] == "assistant" else ":material/person:"
    with st.chat_message(message["role"], avatar=avatar):
        st.caption("Revisiter · Assistant" if message["role"] == "assistant" else "Vous")
        st.markdown(formater_reponse(message["content"]))
        sources_message(message.get("sources", []))
        afficher_mesures(message.get("metrics", {}))
        afficher_preuves(message.get('assessment'), message.get('resolution'))
        if message.get("schema"):
            afficher_schema(message["schema"], cle)
        if message.get("context"):
            with st.expander("Passages fournis à l’assistant"):
                st.code(message["context"], language=None)


def obtenir_graphe(extraits, empreintes, configuration, documents=None):
    """Garder l'encodeur et le graphe dans la session de cet utilisateur."""
    cle_index = (
        empreintes,
        st.session_state.get('espace_id'), utilisateur['id'],
        bool(st.session_state.get('espace_autosave', True)),
        configuration.mode,
        configuration.model,
        configuration.encoder,
        hashlib.sha256(configuration.api_key.encode("utf-8")).hexdigest(),
        st.session_state.get("mode_recherche", "Automatique"),
    )
    if st.session_state.get("cle_index") != cle_index:
        if st.session_state.get("nom_encodeur") != configuration.encoder:
            st.session_state.encodeur = TransformerEmbeddings(configuration.encoder)
            st.session_state.nom_encodeur = configuration.encoder
        graphe = build_question_graph(
            extraits,
            EmbeddingsPersistants(st.session_state.encodeur, stockage, st.session_state.espace_id, jeton,
                configuration.encoder) if st.session_state.get('espace_id') and st.session_state.get('espace_autosave', True) else st.session_state.encodeur,
            create_chat_model(configuration),
            top_k=4,
            max_context_chars=MAX_CONTEXT_CHARS,
            retrieval_mode={"Automatique": "auto", "Approfondie": "semantic", "Lexicale": "lexical"}[
                st.session_state.get("mode_recherche", "Automatique")],
            documents=documents,
            structures=structures_indexees,
        )
        st.session_state.graphe = graphe
        st.session_state.cle_index = cle_index
    return st.session_state.graphe


st.set_page_config(
    page_title="Revisiter · Espace projet", page_icon=":material/code:",
    layout="wide", initial_sidebar_state="auto",
)
appliquer_style()
stockage, jeton, utilisateur = authentifier()

with st.sidebar:
    marque()
    st.html('<div class="rv-sidebar-label">VOTRE PROJET</div>')
    nom_projet = st.text_input("Nom du projet", max_chars=120, placeholder="Ex. Atlas", key='nom_projet')
    enregistrer_espace, copier_espace, sauvegarde_auto = barre_espaces(stockage, jeton, utilisateur)
    st.html('<div class="rv-sidebar-label">FICHIERS À EXPLORER</div>')
    import_projet = selection_import()
    fichiers = import_projet.fichiers
    if not fichiers and st.session_state.get('documents_restaures'):
        fichiers = [FichierProjet(n, c.encode('utf-8')) for n, c in st.session_state.documents_restaures.items()]
        st.caption('Instantané chargé depuis votre espace enregistré.')
    st.caption("300 fichiers · 512 Ko par fichier · 20 Mo au total")
    with st.expander("Formats et limites"):
        st.caption("Code source, configuration, documentation et notebooks Jupyter en UTF-8.")
        st.caption("Formats : " + ", ".join(sorted(ALLOWED_EXTENSIONS)))
    st.divider()
    st.html('<div class="rv-sidebar-label">MOTEUR D’ANALYSE</div>')
    try:
        parametres = load_settings()
        mode_affiche = "Local" if parametres.mode == "local" else "Cloud"
        encodeur_affiche = "CamemBERT" if parametres.encoder == "camembert" else "BERT multilingue"
        st.html(
            '<div class="rv-model"><small>Modèle configuré</small>'
            f'<strong>{escape(parametres.model)}</strong>'
            f'<span class="rv-mode">{mode_affiche}</span>'
            f'<small>{encodeur_affiche}</small></div>'
        )
    except ValueError:
        st.caption("Renseignez le modèle dans votre fichier .env pour commencer la discussion.")
    st.html(
        '<p class="rv-sidebar-note">Les analyses s’appuient sur les fichiers importés. '
        'Les changements proposés sont à examiner et à appliquer dans votre éditeur.</p>'
    )

mode_recherche = controles_performance()
masquer_secrets, exclure_instructions, zone_securite = preferences_securite()
constats_securite, fichiers_ecartes = [], []
extraits = []
documents_bruts = {}
documents = []
empreintes = []
noms_vus = set()
if 'index_documents' not in st.session_state:
    st.session_state.index_documents = {}
index_incremental = IndexIncremental(st.session_state.index_documents, stockage,
    st.session_state.get('espace_id') if sauvegarde_auto or enregistrer_espace or copier_espace else None, jeton)
structures_indexees = {}

if fichiers:
    if len(fichiers) > MAX_FILES:
        st.error(f"Maximum autorisé : {MAX_FILES} fichiers.")
        st.stop()
    if sum(f.size for f in fichiers) > MAX_PROJECT_BYTES:
        st.error("Le projet dépasse la limite de 20 Mo.")
        st.stop()

    for fichier in fichiers:
        if Path(fichier.name).suffix.lower() not in ALLOWED_EXTENSIONS:
            st.error(f"Format non accepté : {fichier.name}")
            continue
        if fichier.name in noms_vus:
            st.error(f"Nom en double : {fichier.name}. Renomme l’un des fichiers.")
            continue
        noms_vus.add(fichier.name)
        if fichier.size > MAX_FILE_BYTES:
            st.error(f"{fichier.name} dépasse la limite de 512 Ko.")
            continue

        donnees = fichier.getvalue()
        if b"\x00" in donnees:
            st.error(f"{fichier.name} contient des données binaires.")
            continue
        try:
            contenu = donnees.decode("utf-8-sig")
        except UnicodeDecodeError:
            st.error(f"{fichier.name} doit être encodé en UTF-8.")
            continue
        if not contenu.strip():
            st.warning(f"{fichier.name} est vide.")
            continue

        examen = examiner_fichier(fichier.name, contenu)
        constats_securite.extend(examen['secrets'] + examen['instructions'])
        if examen['instructions'] and exclure_instructions:
            fichiers_ecartes.append(fichier.name)
            continue
        if masquer_secrets:
            contenu = examen['text']
        if len(contenu.encode('utf-8')) > MAX_FILE_BYTES:
            st.warning(f"{fichier.name} dépasse la limite après protection des valeurs sensibles.")
            continue

        try:
            document, passages, structure = index_incremental.preparer(fichier.name, contenu)
            structures_indexees[fichier.name] = structure
        except ValueError as erreur:
            st.error(f"{fichier.name} : {erreur}")
            continue
        if not passages:
            st.warning(f"{fichier.name} ne contient aucun texte exploitable.")
            continue

        extraits.extend(passages)
        empreintes.append((fichier.name, hashlib.sha256(contenu.encode('utf-8')).hexdigest()))
        documents.append((fichier.name, document))
        documents_bruts[fichier.name] = contenu

afficher_securite(zone_securite, constats_securite, fichiers_ecartes, masquer_secrets)

# Le nom et le contenu analysé déterminent le projet courant.
empreintes = tuple(sorted(empreintes))
identite_projet = (nom_projet.strip(), empreintes)
ancienne_identite = st.session_state.get("identite_projet")
if ancienne_identite != identite_projet:
    annuler_execution()
    for cle in ('benchmark_projet', 'comparaison_versions', 'validation_isolee', '_retour_execution'):
        st.session_state.pop(cle, None)
    st.session_state.messages = []
    st.session_state.pop("vue_ensemble", None)
    st.session_state.pop("ameliorations", None)
    st.session_state.pop("guide", None)
    st.session_state.pop("choix_amelioration", None)
    st.session_state.pop("transformation", None)
    st.session_state.pop('tests_regression', None)
    if ancienne_identite is None or ancienne_identite[1] != empreintes:
        st.session_state.pop("graphe", None)
        st.session_state.pop("cle_index", None)
    if ancienne_identite is not None:
        st.sidebar.info("Projet modifié : une nouvelle discussion commence.")
    st.session_state.identite_projet = identite_projet
reprendre_etat(extraits)
st.sidebar.caption(f'Préparation : {index_incremental.reutilises} fichier(s) réutilisé(s), {index_incremental.recalcules} recalculé(s).')

introduction(nom_projet, len(empreintes))
indicateurs_emplacement = st.empty()
onglet_chat, onglet_vue, onglet_ameliorations, onglet_guide, onglet_fichiers, onglet_export, onglet_carte, onglet_evaluation = st.tabs([
    "Discussion", "Vue d’ensemble", "Améliorations", "Plan guidé", "Fichiers", "Compte rendu", "Cartographie", "Évaluation",
])
with onglet_evaluation:
    afficher_evaluation()
    afficher_benchmark_projet(documents_bruts)
    afficher_diagnostic_requetes()
with onglet_carte:
    afficher_cartographie(documents_bruts, empreintes, structures_indexees)

with onglet_vue:
    st.subheader("Vue d’ensemble des fichiers importés")
    st.caption("Obtiens un aperçu sourcé du code et des éléments qui restent à examiner.")

    if st.button("Préparer la vue d’ensemble", key="preparer_vue", disabled=not extraits or bool(st.session_state.get('_execution_active')), type="primary"):
        try:
            with st.spinner("Préparation de la vue d’ensemble…"):
                configuration = load_settings()
                graphe_vue = build_summary_graph(extraits, create_chat_model(configuration))
                st.session_state.vue_ensemble = graphe_vue.invoke({
                    "project_name": nom_projet.strip(),
                })
        except ERREURS_IA:
            st.error("Impossible de préparer la vue d’ensemble. Vérifie Ollama et la configuration.")

    if "vue_ensemble" in st.session_state:
        vue = st.session_state.vue_ensemble
        st.caption(
            f"Aperçu fondé sur {vue['selected_chunks']} / {vue['total_chunks']} extraits ; "
            f"{len(vue['covered_files'])} / {vue['total_files']} fichiers représentés."
        )
        st.markdown(vue["answer"])
        if vue["omitted_files"]:
            with st.expander("Fichiers non représentés dans cet aperçu"):
                st.code("\n".join(vue["omitted_files"]), language=None)
        with st.expander("Passages fournis pour la vue d’ensemble"):
            st.code(vue["context"], language=None)


    if "vue_ensemble" not in st.session_state:
        etat_vide("layers", "Une vue d’ensemble pour commencer",
                  "Importez vos fichiers puis préparez un aperçu des composants, des technologies "
                  "visibles et des éléments à approfondir.")

with onglet_ameliorations:
    st.subheader("Améliorations proposées")
    st.caption("Examine des propositions justifiées par le code, avec priorité et effort estimés.")

    if st.button("Proposer des améliorations", key="proposer_ameliorations", disabled=not extraits or bool(st.session_state.get('_execution_active')), type="primary"):
        try:
            with st.spinner("Préparation des propositions d’amélioration…"):
                configuration = load_settings()
                graphe_ameliorations = build_recommendation_graph(
                    extraits, create_chat_model(configuration)
                )
                st.session_state.ameliorations = graphe_ameliorations.invoke({
                    "project_name": nom_projet.strip(),
                })
                st.session_state.pop("guide", None)
                st.session_state.pop("choix_amelioration", None)
                st.session_state.pop("transformation", None)
        except ErreurRecommandations as erreur:
            st.error(f"Réponse d’Ollama rejetée : {erreur}")
        except ResponseError as erreur:
            st.error(
                f"Ollama a refusé la requête (code HTTP {erreur.status_code}). "
                "Vérifie que le modèle configuré est disponible."
            )
        except TimeoutException:
            st.error("Ollama n’a pas répondu dans le délai prévu. Réessaie après son chargement.")
        except (ConnectionError, HTTPError, OSError):
            st.error("Impossible de joindre Ollama. Vérifie que l’application Ollama est lancée.")
        except ERREURS_IA:
            st.error(
                "Impossible de préparer des propositions valides. "
                "Vérifie Ollama et la configuration, puis réessaie."
            )

    if "ameliorations" in st.session_state:
        analyse = st.session_state.ameliorations
        st.caption(
            f"Analyse de {analyse['selected_chunks']} / {analyse['total_chunks']} extraits ; "
            f"{len(analyse['covered_files'])} / {analyse['total_files']} fichiers représentés."
        )
        for numero, proposition in enumerate(analyse["recommandations"], start=1):
            with st.expander(f"{numero}. {proposition['titre']}", expanded=True):
                priorite(proposition["priorite"], proposition["effort"])
                st.markdown(f"**Justification :** {proposition['justification']}")
                st.markdown(f"**Changement proposé :** {proposition['proposition']}")
                st.markdown(f"**Bénéfice attendu :** {proposition['benefice']}")
                st.code("\n".join(proposition["references"]), language=None)
        if not analyse["recommandations"]:
            st.info("Aucune amélioration étayée n’a été proposée à partir de ces extraits.")
        st.markdown(f"**Limites de l’analyse :** {analyse['limites']}")
        if analyse["omitted_files"]:
            with st.expander("Fichiers non représentés dans cette analyse"):
                st.code("\n".join(analyse["omitted_files"]), language=None)
        with st.expander("Passages fournis pour les recommandations"):
            st.code(analyse["context"], language=None)


    if "ameliorations" not in st.session_state:
        etat_vide("spark", "Des pistes pour faire évoluer votre code",
                  "Générez des propositions justifiées par les extraits, puis choisissez "
                  "l’amélioration que vous souhaitez travailler.")

with onglet_guide:
    st.subheader("Plan d’amélioration guidé")
    st.caption(
        "Choisis une proposition et suis ses trois étapes. "
        "Tu réalises les actions dans ton éditeur, puis tu indiques ta progression ici. "
        "Enregistre l’espace pour retrouver le plan après fermeture."
    )

    propositions = st.session_state.get("ameliorations", {}).get("recommandations", [])
    guide = st.session_state.get("guide")
    etat = guide["graphe"].get_state(guide["config"]).values if guide else None
    guide_actif = etat is not None and etat["statut"] == "en_cours"

    if propositions:
        choix = st.selectbox(
            "Amélioration à travailler", options=list(range(len(propositions))),
            format_func=lambda i: propositions[i]["titre"], key="choix_amelioration",
            disabled=guide_actif,
        )
        if st.button("Préparer le plan", key="preparer_guide", disabled=guide_actif, type="primary"):
            try:
                with st.spinner("Préparation des trois étapes…"):
                    graphe_guide = build_guided_plan_graph(
                        extraits, propositions[choix]
                    )
                    config_guide = {"configurable": {"thread_id": str(uuid4())}}
                    graphe_guide.invoke({"project_name": nom_projet.strip()}, config_guide)
                    st.session_state.guide = {
                        "graphe": graphe_guide, "config": config_guide, "revision": 0,
                    }
                    st.session_state.pop("transformation", None)
                st.rerun()
            except ErreurGuide as erreur:
                st.error(f"Plan rejeté : {erreur}")
            except ERREURS_IA:
                st.error("Impossible de préparer le plan. Vérifie les passages de la proposition.")
    else:
        st.info("Génère d’abord des propositions d’amélioration pour préparer un plan.")

    if etat:
        afficher_transformation(documents_bruts, etat)
        progression_guide(etat)
        st.markdown(f"**Amélioration choisie :** {etat['recommandation']['titre']}")
        libelles = {"a_faire": "à faire", "confirmee": "confirmée", "sautee": "sautée"}
        st.caption(" · ".join(
            f"{i + 1}. {libelles[statut]}" for i, statut in enumerate(etat["statuts"])
        ))
        decision = None
        if etat["statut"] == "abandonne":
            st.info("Plan abandonné. Les modifications manuelles éventuelles restent dans tes fichiers.")
        elif etat["statut"] == "parcouru":
            confirmees = etat["statuts"].count("confirmee")
            sautees = etat["statuts"].count("sautee")
            st.success(
                f"Parcours terminé : {confirmees} étape(s) confirmée(s), {sautees} sautée(s). "
                "Ces statuts correspondent à tes déclarations ; vérifie le résultat dans ton projet."
            )
            if st.button("Revenir à la dernière étape", key="guide_retour_final"):
                decision = {"action": "retour"}
        else:
            position = etat["position"]
            etape = etat["etapes"][position]
            st.markdown(f"**Étape {position + 1} / 3 — {PHASES[position]}**")
            for champ, libelle in (
                ("objectif", "Objectif"), ("action", "Action manuelle"),
                ("resultat_attendu", "Résultat attendu"), ("verification", "Vérification"),
                ("retour_arriere", "Retour arrière"),
            ):
                st.markdown(f"**{libelle} :** {etape[champ]}")
            st.markdown("**Fichiers concernés :** " + ", ".join(etape["fichiers"]))
            st.code("\n".join(etape["references"]), language=None)
            observation = st.text_area("Observations et résultats de vos essais", max_chars=2000,
                value=etat.get("observations", ["", "", ""])[position],
                key=f"observation_{guide['config']['configurable']['thread_id']}_{position}_{guide['revision']}",
                help="Notez les entrées, les résultats attendus et observés. Ces observations sont déclarées par vous ; l’application n’exécute pas vos tests.")
            colonnes = st.columns(3)
            if colonnes[0].button("J’ai réalisé et vérifié cette étape", key="guide_confirmer", type="primary"):
                decision = {"action": "confirmer", "observation": observation}
            if colonnes[1].button("Sauter cette étape", key="guide_sauter"):
                decision = {"action": "sauter", "observation": observation}
            if colonnes[2].button("Étape précédente", key="guide_retour", disabled=position == 0, type="tertiary"):
                decision = {"action": "retour"}

            with st.expander("Reformuler cette étape"):
                cle = f"{guide['config']['configurable']['thread_id']}_{guide['revision']}"
                with st.form(f"form_reformulation_{cle}"):
                    nouveaux_champs = {}
                    for champ, libelle in (
                        ("objectif", "Objectif"), ("action", "Action manuelle"),
                        ("resultat_attendu", "Résultat attendu"), ("verification", "Vérification"),
                        ("retour_arriere", "Retour arrière"),
                    ):
                        nouveaux_champs[champ] = st.text_area(
                            libelle, value=etape[champ], max_chars=CHAMPS_TEXTE[champ],
                            key=f"reformuler_{champ}_{cle}",
                        )
                    if st.form_submit_button("Enregistrer la reformulation"):
                        decision = {"action": "reformuler", "champs": nouveaux_champs}

        if etat["statut"] != "abandonne":
            st.caption(
                "Revenir ou reformuler remet les étapes concernées et suivantes à faire. "
                "Cela ne restaure pas les fichiers : utilise le retour arrière indiqué si nécessaire."
            )
            if st.button("Abandonner le plan", key="guide_abandonner"):
                decision = {"action": "abandonner"}
        if decision:
            try:
                reprendre_guide(guide["graphe"], guide["config"], decision)
                guide["revision"] += 1
                st.rerun()
            except ErreurGuide as erreur:
                st.error(str(erreur))
        with st.expander("Passages utilisés pour ce plan"):
            st.code(etat["context"], language=None)


    if not etat:
        etat_vide("check", "Avancez une étape à la fois",
                  "Le guide vous aide à préparer, modifier et vérifier le changement choisi. "
                  "Vous gardez la main sur chaque étape.")

with onglet_chat, st.container(border=True, key="chat_panel"):
    with st.container(key="discussion_toolbar"):
        titre_chat, choix_format, action_chat = st.columns([3, 2.1, 1.5], vertical_alignment="center")
        titre_chat.subheader("Discussion avec l’assistant")
        with choix_format:
            mode_reponse = st.segmented_control(
                "Format de réponse", ["Auto", "Texte", "Schéma"], key="format_reponse",
                default="Auto", required=True, label_visibility="collapsed",
                help="Auto choisit le format. Schéma illustre une question précise ; l’onglet Cartographie explore les liens du projet.",
            )
        if action_chat.button("Effacer la discussion", key="effacer_discussion", type="tertiary"):
            st.session_state.messages = []
            effacer_brouillon()
            annuler_execution()
    conversation = st.container(height=600 if st.session_state.messages else "content",
                                border=False, key="conversation")
    question_suggeree = None
    format_suggere = None
    with conversation:
        zone_accueil = st.empty()
        if not st.session_state.messages and not st.session_state.get('_execution_active'):
            with zone_accueil.container():
                accueil_discussion(bool(extraits))
                actions = [
                ("Comprendre le projet", "suggestion_role",
                 "Quel est le rôle de chacun des fichiers importés ? Cite les lignes correspondantes.",
                 "file", "Découvrez le rôle des fichiers et la logique d’ensemble."),
                ("Explorer les fonctions", "suggestion_fonctions",
                 "Explique les principales fonctions visibles et cite leurs références.",
                 "code", "Comprenez les paramètres, les calculs et les résultats."),
                ("Repérer les risques", "suggestion_risques",
                 "Quels problèmes concrets vois-tu dans ces extraits ? Justifie avec le code.",
                 "shield", "Examinez les problèmes possibles, avec leurs références."),
                ("Créer un schéma", "suggestion_schema",
                 "Fais un schéma du fonctionnement du code fourni. Nomme les étapes et cite les lignes.",
                 "diagram", "Visualisez le fonctionnement avec des blocs numérotés."),
                ]
                for debut in range(0, len(actions), 2):
                    for numero, (colonne, action) in enumerate(
                        zip(st.columns(2), actions[debut:debut + 2], strict=True), start=debut + 1
                    ):
                        libelle, cle, texte, symbole, description = action
                        with colonne, st.container(border=True, key=cle.replace("suggestion", "carte")):
                            entete_action(symbole, numero)
                            if st.button(libelle, key=cle, disabled=not extraits or bool(st.session_state.get("_execution_active")), width="stretch"):
                                question_suggeree = texte
                                if cle == "suggestion_schema":
                                    format_suggere = "schema"
                            st.caption(description)
        for numero, message in enumerate(st.session_state.messages):
            afficher_message(message, cle=f"message_{numero}")
    contexte_execution = (utilisateur['id'], st.session_state.get('espace_id'), identite_projet)
    afficher_retour_execution()
    question = saisie_question(contexte_execution, disabled=bool(st.session_state.get('_execution_active')))
    question = question or question_suggeree
    if question and question.strip():
        question = question.strip()
        effacer_brouillon()
        try:
            configuration = load_settings()
            graphe = obtenir_graphe(extraits, empreintes, configuration, documents_bruts)
            entree = {
                'question': question, 'request_started': perf_counter(),
                'response_format': format_suggere or choisir_format(question, mode_reponse),
                'project_name': nom_projet.strip(),
                'history': [{'role': m['role'], 'content': m['content']} for m in st.session_state.messages],
            }
            lancer_execution(graphe, entree, question, contexte_execution, documents_bruts, structures_indexees)
            st.rerun()
        except ERREURS_IA as erreur:
            st.error(message_erreur(erreur))
    with conversation:
        if st.session_state.get('_execution_active'):
            zone_accueil.empty()
            afficher_execution(contexte_execution)

    st.caption("Les passages utilisés pour chaque réponse restent consultables dans la discussion.")

with onglet_fichiers:
    st.subheader("Fichiers du projet")
    afficher_comparaison_versions(documents_bruts)
    st.caption("Retrouvez le contenu importé, avec les références de lignes utilisées dans les analyses.")
    if extraits:
        st.caption(f"{len(extraits)} passage(s) préparé(s) pour la recherche.")
        with st.expander('Arborescence du projet', expanded=True):
            st.code(arborescence(documents_bruts), language=None)
        for nom, document in documents:
            with st.expander(f"Voir le contenu de {nom}"):
                st.code(document, language=None)
    else:
        etat_vide("file", "Votre projet commence ici",
                  "Ajoutez vos fichiers depuis la barre latérale. Le code importé sera consultable "
                  "ici avant de lancer une analyse.")

with onglet_export:
    st.subheader("Compte rendu de la session")
    st.caption(
        "Télécharge la vue d’ensemble, les propositions, le plan et la discussion actuels. "
        "Le fichier Markdown (.md) s’ouvre dans VS Code."
    )

    guide_export = st.session_state.get("guide")
    etat_export = (
        guide_export["graphe"].get_state(guide_export["config"]).values if guide_export else None
    )
    compte_rendu = generer_compte_rendu(
        nom_projet, [nom for nom, _ in empreintes], st.session_state.messages,
        vue_ensemble=st.session_state.get("vue_ensemble"),
        ameliorations=st.session_state.get("ameliorations"),
        guide=etat_export,
        transformation=st.session_state.get("transformation"),
        tests_regression=st.session_state.get('tests_regression'),
    )
    st.download_button(
        "Télécharger le compte rendu (.md)",
        data=compte_rendu.encode("utf-8-sig"),
        file_name="compte_rendu_revisiter.md",
        mime="text/markdown; charset=utf-8",
        key="telecharger_compte_rendu",
        on_click="ignore",
        type="primary",
        disabled=not bool(nom_projet.strip() or empreintes or st.session_state.messages),
    )

    st.caption("Ouvrez le fichier téléchargé dans VS Code ; Ctrl+Shift+V affiche son aperçu.")

# Afficher les indicateurs après les actions pour refléter leur résultat immédiatement.
guide_courant = st.session_state.get("guide")
etat_courant = guide_courant["graphe"].get_state(guide_courant["config"]).values if guide_courant else None
with indicateurs_emplacement:
    indicateurs(len(empreintes), len(extraits),
                st.session_state.get("ameliorations", {}).get("recommandations", []), etat_courant)
st.html('<footer class="rv-footer">revisiter. &nbsp; / &nbsp; Comprendre le code. Préparer la suite.</footer>')
enregistrer_etat(stockage, jeton, nom_projet.strip(), documents_bruts,
    enregistrer_espace, copier_espace, sauvegarde_auto)
