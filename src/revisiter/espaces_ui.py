"""Espaces enregistrés, authentification optionnelle et reprise contrôlée de l’état."""

import hashlib
import json
import sqlite3

import streamlit as st
from dotenv import load_dotenv

from revisiter.index_incremental import VERSION
from revisiter.persistance import instantane, restaurer_guide, valider_instantane
from revisiter.stockage import AccesRefuse, ConflitVersion, Stockage

CLES_PROJET = ('nom_projet', 'messages', 'vue_ensemble', 'ameliorations', 'guide', 'choix_amelioration',
    'transformation', 'tests_regression', 'identite_projet', 'graphe', 'cle_index', 'relations_cle', 'relations_donnees',
    'index_documents', 'documents_restaures', 'espace_id', 'espace_version', 'empreinte_sauvegarde', 'fichiers_projet',
    'archive_projet', 'archive_importee', 'cle_archive', 'git_importe', 'source_import', 'evaluation_rapport')


def vider_projet():
    from revisiter.executions_ui import annuler_execution

    annuler_execution()
    for cle in CLES_PROJET:
        st.session_state.pop(cle, None)
    for cle in ('benchmark_projet', 'comparaison_versions', 'validation_isolee', '_retour_execution'):
        st.session_state.pop(cle, None)


def authentifier():
    load_dotenv('.env', override=False)
    stockage = Stockage()
    token = st.session_state.get('auth_token')
    if stockage.partage:
        if not stockage.administrateur_present():
            st.title('Initialiser l’atelier partagé')
            st.info('Sur le serveur, créez le premier administrateur avec le terminal :')
            st.code('python -m revisiter.comptes', language=None)
            st.stop()
        try:
            utilisateur = stockage.utilisateur(token)
        except AccesRefuse:
            if token is not None:
                from revisiter.executions_ui import annuler_execution
                annuler_execution()
                st.session_state.clear()
            else:
                vider_projet()
            st.title('Connexion à Revisiter')
            with st.form('connexion', clear_on_submit=True):
                identifiant = st.text_input('Identifiant', key='login_identifiant')
                mot = st.text_input('Mot de passe', type='password', key='login_mot')
                envoyer = st.form_submit_button('Se connecter')
            if envoyer:
                try:
                    st.session_state.auth_token = stockage.connexion_utilisateur(identifiant, mot)
                    st.rerun()
                except AccesRefuse as erreur:
                    st.error(str(erreur))
            st.stop()
    else:
        utilisateur = stockage.utilisateur()
    if st.session_state.get('espace_id'):
        try:
            stockage.autorisation_projet(st.session_state.espace_id, token)
        except AccesRefuse as erreur:
            vider_projet()
            st.error(str(erreur))
            st.stop()
    pending = st.session_state.pop('espace_a_charger', None)
    if pending:
        contenu = valider_instantane(pending['snapshot'])
        vider_projet()
        st.session_state.nom_projet = pending['name']
        st.session_state.espace_id = pending['id']
        st.session_state.espace_version = pending['version']
        st.session_state.documents_restaures = contenu['documents']
        st.session_state.restauration_etat = contenu['state']
        st.session_state.restauration_guide = contenu['guide']
    return stockage, token, utilisateur


def barre_espaces(stockage, token, utilisateur):
    enregistrer = copie = False
    with st.sidebar.expander('Espaces de travail enregistrés'):
        st.caption('Les fichiers et la discussion sont conservés localement dans la base de l’atelier lorsque vous enregistrez un espace.')
        espaces = stockage.lister_projets(token)
        if espaces:
            choix = st.selectbox('Espace à ouvrir', [p['id'] for p in espaces],
                format_func=lambda i: next(p['name'] for p in espaces if p['id'] == i), key='espace_selection')
            if st.button('Ouvrir cet espace', key='ouvrir_espace'):
                st.session_state.espace_a_charger = stockage.ouvrir_projet(choix, token)
                st.rerun()
        if st.button('Nouveau projet vide', key='nouvel_espace'):
            vider_projet()
            st.rerun()
        droits = stockage.autorisation_projet(st.session_state.espace_id, token) if st.session_state.get('espace_id') else {'owner': True, 'can_edit': True}
        enregistrer = st.button('Enregistrer cet espace', key='enregistrer_espace', disabled=not droits['can_edit'])
        copie = st.button('Enregistrer une copie distincte', key='copier_espace')
        automatique = st.checkbox('Enregistrer automatiquement les évolutions de cet espace', value=True,
            key='espace_autosave', disabled=not droits['can_edit'])
        if not droits['can_edit']:
            st.caption('Accès en lecture : vous pouvez explorer le projet et enregistrer une copie personnelle.')
        if st.session_state.get('espace_id') and st.button('Vider le cache de cet espace', key='vider_cache'):
            stockage.purger_cache(st.session_state.espace_id, token)
            for cle in ('index_documents', 'graphe', 'cle_index', 'encodeur', 'nom_encodeur', 'relations_cle'):
                st.session_state.pop(cle, None)
            st.success('Cache de votre espace vidé.')
        if stockage.partage and st.session_state.get('espace_id') and droits['owner']:
            with st.form('partager_espace', clear_on_submit=True):
                compte = st.text_input('Compte auquel donner accès')
                role = st.selectbox('Droit sur cet espace', ['lecture', 'edition', 'retirer'])
                partager = st.form_submit_button('Mettre à jour l’accès')
            if partager:
                try:
                    stockage.partager(st.session_state.espace_id, compte, role, token)
                    st.success('Accès mis à jour.')
                except ValueError as erreur:
                    st.error(str(erreur))
    if stockage.partage:
        st.sidebar.caption('Connecté : ' + utilisateur['username'])
        if st.sidebar.button('Se déconnecter', key='deconnexion'):
            from revisiter.executions_ui import annuler_execution
            annuler_execution()
            stockage.deconnecter(token)
            st.session_state.clear()
            st.rerun()
        if utilisateur['role'] == 'admin':
            with st.sidebar.expander('Administration des comptes'):
                st.dataframe(stockage.liste_utilisateurs(token), hide_index=True)
                with st.form('creer_compte', clear_on_submit=True):
                    nom = st.text_input('Identifiant du nouveau compte')
                    mot = st.text_input('Mot de passe initial', type='password')
                    role = st.selectbox('Rôle du compte', ['membre', 'admin'])
                    creer = st.form_submit_button('Créer le compte')
                if creer:
                    try:
                        stockage.creer_utilisateur(token, nom, mot, role)
                        st.success('Compte créé.')
                    except ValueError as erreur:
                        st.error(str(erreur))
    return enregistrer, copie, automatique and droits['can_edit']


def reprendre_etat(extraits):
    etat = st.session_state.pop('restauration_etat', None)
    if etat is not None:
        for cle, valeur in etat.items():
            st.session_state[cle] = valeur
    guide = st.session_state.pop('restauration_guide', None)
    if guide:
        try:
            st.session_state.guide = restaurer_guide(guide, extraits)
        except (ValueError, KeyError, TypeError):
            st.warning('Le guide enregistré n’a pas pu être repris ; la discussion et les fichiers restent disponibles.')


def enregistrer_etat(stockage, token, nom, documents, demander, copie, automatique):
    actif = st.session_state.get('espace_id')
    if not (demander or copie or (actif and automatique)):
        return
    snapshot = instantane(documents, st.session_state)
    empreinte = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if not demander and not copie and st.session_state.get('empreinte_sauvegarde') == empreinte:
        return
    try:
        identifiant, version = stockage.enregistrer_projet(nom or 'Projet sans nom', snapshot,
            projet=None if copie else actif, version=st.session_state.get('espace_version'), token=token)
        st.session_state.espace_id, st.session_state.espace_version = identifiant, version
        st.session_state.empreinte_sauvegarde = empreinte
        if not actif or copie:
            memoire = st.session_state.get('index_documents', {})
            cles = [hashlib.sha256((VERSION + n + '\x00' + c).encode()).hexdigest() for n, c in documents.items()]
            try:
                stockage.ecrire_caches(identifiant, 'document', {k: memoire[k] for k in cles if k in memoire}, token)
            except sqlite3.Error:
                st.sidebar.warning('Espace enregistré ; son cache sera recalculé à la prochaine ouverture.')
        if demander or copie:
            st.sidebar.success('Espace enregistré. Fichiers, discussion et guide pourront être rouverts.')
    except (ConflitVersion, AccesRefuse, ValueError) as erreur:
        st.sidebar.error(str(erreur))
    except (sqlite3.Error, OSError):
        st.sidebar.error('Impossible d’enregistrer l’espace. Vérifiez le dossier de données et réessayez ; la session reste ouverte.')
