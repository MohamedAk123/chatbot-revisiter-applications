"""Initialisation du premier administrateur depuis le terminal du serveur."""

from getpass import getpass

from dotenv import load_dotenv

from revisiter.stockage import Stockage


def main():
    load_dotenv('.env', override=False)
    stockage = Stockage()
    if stockage.administrateur_present():
        raise SystemExit('Un administrateur existe déjà. Connectez-vous dans l’application.')
    identifiant = input('Identifiant du premier administrateur : ').strip()
    mot = getpass('Mot de passe (15 caractères minimum) : ')
    if mot != getpass('Confirmer le mot de passe : '):
        raise SystemExit('Les mots de passe diffèrent.')
    stockage.initialiser_admin(identifiant, mot)
    print('Administrateur créé. Activez REVISITER_AUTH_ENABLED=true dans .env puis relancez Streamlit.')


if __name__ == '__main__':
    main()
