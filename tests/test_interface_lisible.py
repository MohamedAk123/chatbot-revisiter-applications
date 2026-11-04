"""La présentation conserve les preuves et ne modifie pas les blocs de code."""

import re
from html.parser import HTMLParser

from revisiter.interface import formater_reponse, sources_message


def test_regroupement_des_sources_conserve_toutes_les_references_et_dedoublonne(monkeypatch):
    blocs = []
    monkeypatch.setattr('streamlit.html', lambda contenu: blocs.append(contenu))
    sources_message(['a.py:L1-L2', 'a.py:L4-L4', 'a.py:L4-L4', 'b.py:L3-L3'])

    class Lecture(HTMLParser):
        def __init__(self):
            super().__init__()
            self.references = []

        def handle_starttag(self, tag, attrs):
            attributs = dict(attrs)
            if tag == 'span' and 'title' in attributs:
                self.references.append(attributs['title'])

    lecture = Lecture()
    lecture.feed(blocs[0])
    assert lecture.references == ['a.py:L1-L2', 'a.py:L4-L4', 'b.py:L3-L3']
    assert '2 fichier(s) · 3 passage(s)' in blocs[0]
    assert '<summary>' in blocs[0]


def test_titres_de_fichiers_separes_sans_changer_le_texte_ni_un_bloc_de_code():
    texte = 'Rôle visible des fichiers — 1/1 fichiers décrits :\n\n**` demo.py `**\nRetour : `sum(prix)` [demo.py:L1-L2].'
    lisible = formater_reponse(texte)
    assert '**` demo.py `**\n\nRetour' in lisible
    assert re.sub(r'\s+', ' ', lisible) == re.sub(r'\s+', ' ', texte)
    code = 'Proposition :\n```python\n**`nom`**\nune_ligne\n```'
    assert formater_reponse(code) == code
