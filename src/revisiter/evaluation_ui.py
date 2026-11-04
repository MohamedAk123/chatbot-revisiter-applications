import json

import streamlit as st

from revisiter.config import load_settings
from revisiter.evaluation import evaluer_cas, rapport
from revisiter.jeux_evaluation import jeu_reference
from revisiter.ollama_client import create_chat_model


def afficher_evaluation():
    st.subheader('Évaluer la qualité et la rapidité')
    st.caption('30 questions pédagogiques : 15 vérifications statiques et 15 raisonnements à relire. Aucun fichier de ces exemples n’est exécuté.')
    cas = jeu_reference()
    selection = st.multiselect('Cas à évaluer', [c['id'] for c in cas], default=[c['id'] for c in cas if c['kind'] == 'static'], key='evaluation_selection')
    inclure = st.checkbox('Autoriser les appels à Ollama local pour les cas interprétatifs', key='evaluation_llm')
    if st.button('Exécuter les vérifications sélectionnées', key='executer_evaluation', disabled=not selection or bool(st.session_state.get('_execution_active'))):
        modele = None
        if inclure:
            configuration = load_settings()
            if configuration.mode != 'local':
                st.error('Ce banc d’essai est limité à Ollama local.')
                return
            modele = create_chat_model(configuration)
        resultats = []
        progression = st.progress(0)
        choisis = [c for c in cas if c['id'] in selection]
        for i, c in enumerate(choisis):
            with st.spinner('Vérification : ' + c['id']):
                resultats.append(evaluer_cas(c, modele))
            st.session_state.evaluation_rapport = rapport(resultats)
            progression.progress((i + 1) / len(choisis))
    resultat = st.session_state.get('evaluation_rapport')
    if resultat:
        st.caption(f"{resultat['checks_passed']} vérification(s) mécanique(s) réussie(s) / {resultat['executed']} exécutée(s) · {resultat['human_review_pending']} réponse(s) à relire")
        st.dataframe([{k: r.get(k) for k in ('id', 'status', 'checks_passed', 'first_text_s', 'total_s')} for r in resultat['cases']], hide_index=True)
        for r in resultat['cases']:
            if 'answer' in r:
                with st.expander(r['id'] + ' · ' + r['status']):
                    st.text(r['answer'])
                    st.caption('Critère de revue : ' + r['rubric'])
        st.download_button('Télécharger le rapport d’évaluation', data=json.dumps(resultat, ensure_ascii=False, indent=2), file_name='evaluation.json', on_click='ignore', key='export_evaluation')
    st.download_button('Télécharger le jeu des 30 questions', data=json.dumps(cas, ensure_ascii=False, indent=2), file_name='questions_reference.json', on_click='ignore', key='export_jeu')
