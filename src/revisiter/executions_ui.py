"""Afficher les travaux sans bloquer la page ; validation unique dans l'historique."""

from uuid import uuid4

import streamlit as st

from revisiter.diagnostic_requetes import consigner_trace
from revisiter.executions import TERMINES, gestionnaire_executions
from revisiter.interface import formater_reponse
from revisiter.memoire_conversation import resoudre_suivi
from revisiter.workflow import iterer_reponse


def proprietaire_execution():
    if '_execution_owner' not in st.session_state:
        st.session_state._execution_owner = str(uuid4())
    return st.session_state._execution_owner


def annuler_execution():
    identifiant = st.session_state.pop('_execution_active', None)
    proprietaire = st.session_state.get('_execution_owner')
    if identifiant and proprietaire:
        try:
            gestionnaire_executions().annuler(identifiant, proprietaire)
        except PermissionError:
            pass


def lancer_execution(graphe, entree, question, contexte, documents, structures=None):
    resolution = resoudre_suivi(question, entree.get('history', []), documents, structures)
    proprietaire = proprietaire_execution()

    def travail(controle, emettre):
        if resolution['clarification']:
            reponse = resolution['clarification']
            emettre(reponse)
            return {'answer': reponse, 'sources': [], 'context': '', 'resolution': resolution,
                    'metrics': {'method': 'clarification', 'generation_ms': 0}}
        resultat = {}
        for fragment in iterer_reponse(graphe, {**entree, 'question': resolution['question'], 'controle': controle}, resultat):
            emettre(fragment)
        if not isinstance(resultat.get('answer'), str):
            raise TypeError('Le moteur n’a pas produit de résultat complet.')
        resultat['resolution'] = resolution
        return resultat

    identifiant = gestionnaire_executions().lancer(proprietaire, contexte, question, travail)
    st.session_state._execution_active = identifiant
    # Les réponses statiques se terminent souvent immédiatement ; même parcours asynchrone.
    gestionnaire_executions().attendre(identifiant, proprietaire, .3)
    return identifiant


@st.fragment(run_every=.4)
def afficher_execution(contexte):
    identifiant = st.session_state.get('_execution_active')
    if not identifiant:
        return
    proprietaire = proprietaire_execution()
    gestionnaire = gestionnaire_executions()
    try:
        t = gestionnaire.lire(identifiant, proprietaire)
    except PermissionError:
        st.session_state.pop('_execution_active', None)
        st.warning('Cette requête n’est plus disponible. Vous pouvez la renvoyer.')
        return
    if t['context'] != contexte:
        annuler_execution()
        return
    if t['status'] in TERMINES:
        resultat = t['result'] or {}
        mesures = {**resultat.get('metrics', {}), 'queue_ms': t['queue_ms'], 'total_ms': t['elapsed_ms']}
        st.session_state.traces_requetes = consigner_trace(st.session_state.get('traces_requetes', []),
                                                          identifiant, t['status'], mesures)
        if t['status'] == 'terminé':
            deja = any(m.get('request_id') == identifiant for m in st.session_state.messages)
            if not deja:
                assistant = {'role': 'assistant', 'content': resultat['answer'], 'sources': resultat.get('sources', []),
                             'context': resultat.get('context', ''), 'metrics': mesures,
                             'assessment': resultat.get('assessment'), 'resolution': resultat.get('resolution'),
                             'request_id': identifiant}
                if resultat.get('schema'):
                    assistant['schema'] = resultat['schema']
                st.session_state.messages.extend([
                    {'role': 'user', 'content': t['question'], 'request_id': identifiant}, assistant])
        else:
            st.session_state._retour_execution = ('error', t['error']) if t['status'] == 'erreur' else (
                'info', 'Génération annulée. La réponse partielle n’a pas été enregistrée.')
        gestionnaire.oublier(identifiant, proprietaire)
        st.session_state.pop('_execution_active', None)
        st.rerun()
    with st.chat_message('user', avatar=':material/person:'):
        st.caption('Vous')
        st.text(t['question'])
    with st.chat_message('assistant', avatar=':material/auto_awesome:'):
        st.caption('Revisiter · Assistant — ' + t['phase'])
        if t['text']:
            st.markdown(formater_reponse(t['text']))
        else:
            st.info('Requête en attente ou en préparation. Vous pouvez continuer à consulter le projet.')
        if st.button('Annuler la génération', key='annuler_generation_' + identifiant,
                     disabled=t['status'] == 'annulation demandée', icon=':material/stop_circle:'):
            gestionnaire.annuler(identifiant, proprietaire)
            st.info('Annulation demandée. Le flux sera fermé et aucun résultat partiel ne sera conservé.')


def afficher_retour_execution():
    retour = st.session_state.pop('_retour_execution', None)
    if retour:
        getattr(st, retour[0])(retour[1])
