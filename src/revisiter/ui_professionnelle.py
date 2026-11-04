"""Diagnostic, mesures et revue de transformation dans l'interface existante."""

import hashlib
import json
from pathlib import Path

import httpx
import streamlit as st
from ollama import ResponseError

from revisiter.config import load_settings
from revisiter.non_regression import PROFILS, proposer_tests
from revisiter.ollama_client import create_chat_model
from revisiter.qualite_ui import afficher_validation_isolee
from revisiter.robustesse import message_erreur
from revisiter.securite_analyse import MARQUEUR
from revisiter.tests_explicites import construire_tests_explicites
from revisiter.transformations import (
    ErreurTransformation,
    empreinte,
    portees_transformation,
    proposer_transformation,
)


def controles_performance():
    with st.sidebar.expander("Performance et diagnostic"):
        choix = st.selectbox("Recherche", ["Automatique", "Approfondie", "Lexicale"],
            key="mode_recherche", help="Automatique repère le code et évite le chargement NLP inutile. Approfondie compare tous les passages avec l’encodeur ; sa préparation peut être longue.")
        if st.button("Tester Ollama", key="diagnostic_ollama"):
            try:
                settings = load_settings()
                if settings.mode != "local":
                    st.info("Diagnostic de disponibilité proposé pour le mode local uniquement.")
                else:
                    reponse = httpx.get(settings.base_url + "/api/tags", timeout=3, trust_env=False)
                    reponse.raise_for_status()
                    noms = {m.get("name", m.get("model")) for m in reponse.json().get("models", [])}
                    nom = settings.model if ":" in settings.model else settings.model + ":latest"
                    if nom in noms:
                        st.success("Serveur accessible et modèle présent. Ce test ne charge pas le modèle.")
                    else:
                        st.warning("Serveur accessible, mais le modèle configuré n’est pas présent dans la liste.")
            except (ValueError, httpx.HTTPError, OSError) as erreur:
                st.error(message_erreur(erreur))
    return {"Automatique": "auto", "Approfondie": "semantic", "Lexicale": "lexical"}[choix]


def afficher_mesures(metrics):
    if not metrics:
        return
    noms = {"static": "Analyse statique", "lexical": "Repérage du code", "hybrid": "Recherche hybride", "full_context": "Contexte complet"}
    st.caption(f"{noms.get(metrics.get('method'), 'Recherche sémantique')} · "
               f"premier texte {metrics.get('first_text_ms', 0) / 1000:.2f} s · "
               f"total {metrics.get('total_ms', 0) / 1000:.2f} s")
    if metrics.get("warning"):
        st.caption(metrics["warning"])
    if metrics.get("citation_status") == "missing":
        st.caption("Le modèle n’a pas cité de lignes dans son texte. Les passages fournis restent à examiner.")
    elif metrics.get("citation_status") == "checked":
        st.caption("Les plages citées appartiennent aux passages fournis ; ce contrôle ne vérifie pas le raisonnement.")


def afficher_transformation(documents, etat):
    noms = sorted({ref.rsplit(":L", 1)[0] for ref in etat["sources"]} & set(documents))
    noms = [n for n in noms if Path(n).suffix.lower() != ".ipynb"]
    if not noms:
        return
    with st.expander("Préparer une proposition de code à relire"):
        st.caption("Choisissez un fichier et des critères précis. Le programme prépare un diff ; les fichiers de votre projet restent à modifier dans votre éditeur.")
        fichier = st.selectbox("Fichier ciblé", noms, key="transformation_fichier")
        original = documents[fichier]
        if MARQUEUR in original:
            st.warning('Ce fichier contient des valeurs masquées. Préparez un fichier sans secrets avant une proposition de remplacement, pour préserver la configuration réelle.')
            return
        portees = portees_transformation(fichier, original, etat["sources"])
        if not portees:
            st.info("Aucune portion de 6 000 caractères maximum disponible. Choisissez une recommandation plus ciblée.")
            return
        choix = st.selectbox("Portion à remplacer", range(len(portees)), format_func=lambda i: portees[i]["label"], key="transformation_portion")
        source = portees[choix]["reference"]
        debut, fin = (int(n) for n in source.rsplit(":L", 1)[1].split("-L"))
        st.caption("La proposition remplacera uniquement cette portion du fichier importé.")
        st.code("\n".join(original.splitlines()[debut - 1:fin]), language=None)
        cle = hashlib.sha256((empreinte(original) + source + json.dumps(etat["recommandation"], sort_keys=True)).encode()).hexdigest()[:12]
        with st.form("contrat_" + cle):
            objectif = st.text_area("Changement demandé", value=etat["recommandation"]["proposition"], max_chars=1000)
            contraintes = st.text_area("Comportements à préserver", value="Conserver les noms, les paramètres et les comportements documentés hors de ce changement. Ne pas ajouter de dépendance.", max_chars=1500)
            criteres = st.text_area("Critères de réussite et cas à vérifier", value=etat["recommandation"]["benefice"], max_chars=1500,
                help="Précisez les entrées, les résultats attendus et l’état après une erreur éventuelle.")
            api = st.checkbox("Autoriser un changement de noms ou de paramètres", value=False)
            demander = st.form_submit_button("Préparer le diff et les cas de vérification", disabled=bool(st.session_state.get('_execution_active')))
        if demander:
            try:
                with st.spinner("Préparation et contrôle de la proposition de code…"):
                    modele = create_chat_model(load_settings(), context_window=8192)
                    proposition = proposer_transformation(modele, fichier, original,
                        [source],
                        objectif, contraintes, criteres, autoriser_api=api)
                st.session_state.transformation = proposition
                st.session_state.transformation["scope_key"] = cle
            except ErreurTransformation as erreur:
                st.error(str(erreur))
            except (httpx.HTTPError, ResponseError, ConnectionError, ValueError, RuntimeError, OSError, ImportError) as erreur:
                st.error(message_erreur(erreur))
        proposition = st.session_state.get("transformation")
        if not proposition or proposition.get("scope_key") != cle or proposition["file"] != fichier or proposition["base_sha256"] != empreinte(original):
            return
        st.text(proposition["title"])
        st.write(proposition["summary"])
        if proposition["syntax"] == "ok":
            st.success("Syntaxe reconnue par le parseur. Aucun test ni code du projet n’a été exécuté.")
        else:
            st.warning("Syntaxe non contrôlée pour ce format ou parseur indisponible. Une vérification dans vos outils est nécessaire.")
        st.caption("Contrôle des déclarations : " + proposition["api"] + ". La compatibilité complète reste à vérifier.")
        st.code(proposition["diff"], language="diff")
        if proposition.get("tests_origin") == "user_criteria":
            st.caption("Critères fournis dans le formulaire, à transformer en tests dans votre projet.")
        for i, cas in enumerate(proposition["tests"], start=1):
            st.text(f"Cas proposé {i} : {cas}")
        st.download_button("Télécharger le diff de revue", data=proposition["diff"].encode("utf-8"),
            file_name=Path(fichier).name + ".diff", key="telecharger_diff", on_click="ignore")
        st.download_button("Télécharger le fichier proposé", data=proposition["code"].encode("utf-8"),
            file_name=Path(fichier).name, key="telecharger_code", on_click="ignore")
        if Path(fichier).suffix in PROFILS:
            with st.expander('Préparer des tests de non-régression'):
                module_python = None
                if Path(fichier).suffix == '.py':
                    module_python = st.text_input('Module Python importable', value=fichier.removesuffix('.py').removeprefix('src/').replace('/', '.'), key='module_tests_' + cle)
                    mode_tests = st.radio('Préparation des tests', ['Cas explicites', 'Proposition Ollama à relire'], key='mode_tests_' + cle)
                else:
                    mode_tests = 'Proposition Ollama à relire'
                if mode_tests == 'Cas explicites':
                    st.caption('Décrivez au moins deux cas en JSON. Chaque cas précise function, args, expected ou raises ; setup et after portent sur les variables globales du module.')
                    cas_json = st.text_area('Cas de test JSON', value='', max_chars=12000, key='cas_tests_' + cle,
                        placeholder='[{"name":"normal","function":"votre_fonction","args":[2],"expected":3}, {"name":"limite","function":"votre_fonction","args":[-1],"raises":"ValueError"}]')
                    if st.button('Construire les tests depuis ces cas', key='construire_tests'):
                        try:
                            st.session_state.tests_regression = construire_tests_explicites(proposition, cas_json, module_python)
                        except (ValueError, TypeError, SyntaxError) as erreur:
                            st.error(str(erreur))
                else:
                    st.caption('Le modèle peut se tromper sur les fixtures et les attentes. La syntaxe et certains noms sont contrôlés ; une revue et une exécution dans votre environnement restent nécessaires.')
                attentes = st.text_area('Cas normal, cas limite et comportements à conserver',
                    value=proposition.get('contract', {}).get('criteres', ''), max_chars=2500, key='criteres_tests_' + cle)
                if st.button('Générer le fichier de tests à relire avec Ollama', key='generer_tests', disabled=mode_tests == 'Cas explicites' or bool(st.session_state.get('_execution_active'))):
                    try:
                        with st.spinner('Préparation du fichier de tests…'):
                            tests = proposer_tests(create_chat_model(load_settings(), context_window=8192), proposition, attentes, module_python=module_python)
                        st.session_state.tests_regression = tests
                    except (ValueError, RuntimeError, httpx.HTTPError, ResponseError, ConnectionError, OSError) as erreur:
                        st.error(str(erreur) if isinstance(erreur, ValueError) else message_erreur(erreur))
                tests = st.session_state.get('tests_regression')
                if tests and tests['target'] == fichier and tests['target_sha256'] == empreinte(proposition['code']):
                    st.caption(tests['framework'] + ' · syntaxe ' + tests['syntax'] + ' · tests non exécutés, imports et attentes à vérifier')
                    st.code(tests['code'], language=None)
                    st.download_button('Télécharger les tests', data=tests['code'], file_name=tests['file'], key='export_tests', on_click='ignore')
                    afficher_validation_isolee(documents, proposition, tests)
