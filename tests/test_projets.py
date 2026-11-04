import io
import stat
import zipfile

import pytest

from revisiter.projets import (
    FichierProjet,
    chemin_relatif,
    fichiers_importes,
    lire_github,
    lire_zip,
)


def archive(fichiers):
    flux = io.BytesIO()
    with zipfile.ZipFile(flux, 'w', zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)
    return flux.getvalue()


def test_zip_arborescence_homonymes_exclusions_et_aucune_extraction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resultat = lire_zip(archive({'repo/src/a.py': 'x = 1', 'repo/tests/a.py': 'x = 2',
        'repo/.env': 'SECRET', 'repo/node_modules/a.js': 'SECRET', 'repo/image.png': b'\x00'}))
    assert [f.name for f in resultat.fichiers] == ['src/a.py', 'tests/a.py']
    assert len(resultat.ignores) == 3
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('nom', ['../a.py', '/a.py', 'C:/a.py', 'a/../b.py', 'a\n.py', 'a//b.py'])
def test_chemins_non_relatifs_ou_ambigus_rejetes(nom):
    with pytest.raises(ValueError):
        chemin_relatif(nom)


def test_zip_invalides_liens_et_bombes_rejetes():
    with pytest.raises(ValueError):
        lire_zip(b'pas un zip')
    with pytest.raises(ValueError):
        lire_zip(archive({'../a.py': 'x=1'}))
    flux = io.BytesIO()
    with zipfile.ZipFile(flux, 'w') as z:
        lien = zipfile.ZipInfo('a.py')
        lien.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(lien, 'autre.py')
    resultat = lire_zip(flux.getvalue())
    assert not resultat.fichiers and 'symbolique' in resultat.ignores[0][1]


def test_selection_utf8_limites_et_doublons():
    resultat = fichiers_importes([FichierProjet('src/a.py', b'x=1'), FichierProjet('b.py', b'\xff'), FichierProjet('c.py', b'\x00')])
    assert len(resultat.fichiers) == 1 and len(resultat.ignores) == 2
    with pytest.raises(ValueError, match='casse'):
        fichiers_importes([FichierProjet('a.py', b'x=1'), FichierProjet('A.py', b'x=2')])


@pytest.mark.parametrize('url', ['http://github.com/a/b', 'https://127.0.0.1/a/b', 'https://github.com@evil.test/a/b',
    'https://github.com/a/b?token=SECRET', 'https://user:secret@github.com/a/b'])
def test_git_ne_permet_pas_un_hote_ou_des_identifiants_arbitraires(url):
    with pytest.raises(ValueError):
        lire_github(url)
