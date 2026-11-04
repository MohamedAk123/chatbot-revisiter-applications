import pytest

from revisiter.stockage import AccesRefuse, ConflitVersion, Stockage

MOT = 'exemple-test-uniquement-2026'


@pytest.fixture
def comptes(tmp_path):
    stockage = Stockage(tmp_path, partage=True)
    stockage.initialiser_admin('alice', MOT)
    alice = stockage.connexion_utilisateur('alice', MOT)
    stockage.creer_utilisateur(alice, 'bob', MOT)
    bob = stockage.connexion_utilisateur('bob', MOT)
    return stockage, alice, bob


def test_projets_prives_partage_lecture_edition_retrait_et_cache_isole(comptes):
    s, alice, bob = comptes
    projet, version = s.enregistrer_projet('Atlas', {'documents': {'a.py': 'x=1'}}, token=alice)
    assert not s.lister_projets(bob)
    with pytest.raises(AccesRefuse):
        s.ouvrir_projet(projet, bob)
    s.partager(projet, 'bob', 'lecture', alice)
    assert s.ouvrir_projet(projet, bob)['snapshot']['documents']['a.py'] == 'x=1'
    with pytest.raises(AccesRefuse):
        s.enregistrer_projet('Atlas', {}, projet=projet, version=version, token=bob)
    with pytest.raises(AccesRefuse):
        s.partager(projet, 'alice', 'edition', bob)
    s.ecrire_cache(projet, 'vecteur', 'cle', [1.0], alice)
    assert s.lire_cache(projet, 'vecteur', 'cle', bob) is None
    s.partager(projet, 'bob', 'edition', alice)
    _, nouvelle = s.enregistrer_projet('Atlas', {'documents': {}}, projet=projet, version=version, token=bob)
    assert nouvelle == version + 1
    s.partager(projet, 'bob', 'retirer', alice)
    with pytest.raises(AccesRefuse):
        s.lire_cache(projet, 'vecteur', 'cle', bob)


def test_conflit_de_version_ne_remplace_pas_la_derniere_sauvegarde(comptes):
    s, alice, _ = comptes
    projet, version = s.enregistrer_projet('Atlas', {'valeur': 1}, token=alice)
    s.enregistrer_projet('Atlas', {'valeur': 2}, projet=projet, version=version, token=alice)
    with pytest.raises(ConflitVersion):
        s.enregistrer_projet('Atlas', {'valeur': 3}, projet=projet, version=version, token=alice)
    assert s.ouvrir_projet(projet, alice)['snapshot'] == {'valeur': 2}


def test_mots_de_passe_et_sessions_non_stockes_en_clair_expiration_deconnexion(comptes):
    s, alice, bob = comptes
    with s.connexion() as db:
        hash_session = db.execute('SELECT token_hash FROM sessions').fetchone()[0]
        hash_mot = db.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()[0]
        assert hash_session not in {alice, bob} and hash_mot != MOT
        db.execute('UPDATE sessions SET expires=0')
    with pytest.raises(AccesRefuse):
        s.utilisateur(alice)
    token = s.connexion_utilisateur('alice', MOT)
    s.deconnecter(token)
    with pytest.raises(AccesRefuse):
        s.utilisateur(token)


def test_echecs_authentification_persistes_et_initialisation_non_repetable(comptes):
    s, _alice, _ = comptes
    for _ in range(5):
        with pytest.raises(AccesRefuse):
            s.connexion_utilisateur('bob', 'incorrect')
    with s.connexion() as db:
        assert db.execute("SELECT failures FROM users WHERE username='bob'").fetchone()[0] == 5
    with pytest.raises(AccesRefuse):
        s.connexion_utilisateur('bob', MOT)
    with pytest.raises(AccesRefuse):
        s.initialiser_admin('intrus', MOT)
    with pytest.raises(AccesRefuse):
        s.creer_utilisateur('mauvais-token', 'intrus', MOT)


def test_mode_personnel_ne_donne_pas_acces_aux_projets_partages(comptes, tmp_path):
    s, alice, _ = comptes
    projet, _ = s.enregistrer_projet('Privé', {}, token=alice)
    personnel = Stockage(tmp_path, partage=False)
    assert not personnel.lister_projets()
    with pytest.raises(AccesRefuse):
        personnel.ouvrir_projet(projet)
