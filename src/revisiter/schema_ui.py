"""Afficher un schéma déjà validé, sans le régénérer lors des interactions."""

import streamlit as st

from revisiter.schemas import code_mermaid


def afficher_schema(schema, cle):
    if not schema["noeuds"]:
        st.info("Les extraits fournis ne permettent pas de dessiner le schéma demandé.")
        return
    code = code_mermaid(schema)
    st.mermaid_chart(code, width="stretch", alt=schema["titre"])
    st.caption("Schéma fondé sur les extraits fournis ; les relations restent à vérifier dans le code.")
    with st.expander("Sources et code du schéma"):
        st.code("\n".join(schema["references"]), language=None)
        st.code(code, language="mermaid")
    st.download_button(
        "Télécharger le schéma (.mmd)", data=code.encode("utf-8"),
        file_name="schema_revisiter.mmd", mime="text/plain; charset=utf-8",
        key=f"schema_{cle}", on_click="ignore",
    )
