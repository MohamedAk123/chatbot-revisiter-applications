import streamlit as st

from revisiter.relations import analyser_impact, construire_relations, texte_impact
from revisiter.schemas import _libelle_mermaid


def obtenir_relations(documents, empreintes, structures=None):
    if st.session_state.get('relations_cle') != empreintes:
        st.session_state.relations_donnees = construire_relations(documents, structures)
        st.session_state.relations_cle = empreintes
    return st.session_state.relations_donnees


def afficher_cartographie(documents, empreintes, structures=None):
    st.subheader('Cartographie et impact des changements')
    if not documents:
        st.info('Importez un projet pour explorer ses relations.')
        return
    graphe = obtenir_relations(documents, empreintes, structures)
    genre = st.selectbox('Type d’élément', ['Fonctions', 'Classes', 'Fichiers'], key='carte_genre')
    fonctions = [n for n in graphe['nodes'].values() if n['kind'] == {'Fonctions': 'function', 'Classes': 'class', 'Fichiers': 'file'}[genre]]
    if not fonctions:
        st.info('Aucun élément de ce type repéré dans ces fichiers.')
        return
    filtres = st.multiselect('Limiter aux fichiers', sorted(documents), key='carte_filtres')
    if filtres:
        fonctions = [n for n in fonctions if n['file'] in filtres]
    if not fonctions:
        st.info('Aucun élément ne correspond aux fichiers sélectionnés.')
        return
    par_id = {n['id']: n for n in fonctions}
    selection = st.dataframe([{'Fichier': n['file'], 'Élément': n['name'], 'Ligne': n.get('line', 1)} for n in fonctions],
        hide_index=True, height=180, on_select='rerun', selection_mode='single-row', key='carte_selection_ligne')
    lignes_selectionnees = selection.get('selection', {}).get('rows', [])
    signature_selection = (tuple(par_id), tuple(lignes_selectionnees))
    if lignes_selectionnees and signature_selection != st.session_state.get('carte_derniere_ligne'):
        if lignes_selectionnees[0] < len(fonctions):
            st.session_state.carte_cible = fonctions[lignes_selectionnees[0]]['id']
        st.session_state.carte_derniere_ligne = signature_selection
    st.caption('Cliquez sur une ligne ou choisissez un élément pour consulter son code et son impact.')
    cible = st.selectbox('Élément à explorer', list(par_id),
        format_func=lambda i: par_id[i]['file'] + ' · ' + par_id[i]['name'], key='carte_cible')
    noeud = par_id[cible]
    resultat = analyser_impact(graphe, cible)
    st.markdown(texte_impact(resultat))
    voisins = [l for l in graphe['edges'] if cible in {l['from'], l['to']}][:40]
    ids = list(dict.fromkeys([cible] + [n for l in voisins for n in (l['from'], l['to'])]))
    codes = {identifiant: 'n' + str(i) for i, identifiant in enumerate(ids)}
    lignes = ['---', 'config:', '  htmlLabels: false', '  securityLevel: strict', '---', 'flowchart LR']
    for identifiant in ids:
        nom = _libelle_mermaid(graphe['nodes'][identifiant]['name'][:80])
        lignes.append(codes[identifiant] + '["' + nom + '"]')
    for lien in voisins:
        fleche = '-.->' if lien['confidence'] == 'possible' else '-->'
        libelle = {'contains': 'contient', 'import': 'importe', 'call': 'appelle'}[lien['kind']]
        lignes.append(f"{codes[lien['from']]} {fleche}|{libelle}| {codes[lien['to']]}")
    st.mermaid_chart('\n'.join(lignes))
    st.caption('Liens pleins : résolution syntaxique ; pointillés : candidats possibles. Le schéma est limité au voisinage de la sélection.')
    st.caption('Choisissez un autre élément pour actualiser le schéma et son code.')
    debut = noeud.get('line', 1)
    fin = min(noeud.get('end_line', len(documents[noeud['file']].splitlines())), debut + 100)
    st.code('\n'.join(f'L{i}: {ligne}' for i, ligne in enumerate(documents[noeud['file']].splitlines()[debut - 1:fin], start=debut)), language=None)
    if graphe['excluded']:
        st.caption('Fichiers non analysés : ' + ', '.join(graphe['excluded']))
    if graphe['limited']:
        st.warning('La cartographie a atteint sa limite ; affinez les fichiers importés.')
    st.download_button('Télécharger l’analyse d’impact', data=texte_impact(resultat), file_name='impact.md', on_click='ignore', key='export_impact')
