"""Importer un instantané textuel, sans extraction disque ni exécution du dépôt."""

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from time import monotonic
from urllib.parse import quote, urlsplit

import httpx

from revisiter.ingestion import ALLOWED_EXTENSIONS

MAX_FILES = 300
MAX_FILE_BYTES = 512_000
MAX_PROJECT_BYTES = 20_000_000
MAX_ARCHIVE_BYTES = 30_000_000
MAX_ENTRIES = 5000
EXCLUS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'build',
          'target', 'vendor', '.idea', '.pytest_cache', '.mypy_cache'}
NOMS_EXCLUS = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock',
               'id_rsa', 'id_ed25519', 'credentials.json', 'service-account.json'}


def chemin_relatif(nom):
    if not isinstance(nom, str) or len(nom) > 400 or not nom or any(ord(c) < 32 for c in nom):
        raise ValueError('Chemin de fichier invalide.')
    nom = nom.replace('\\', '/')
    chemin = PurePosixPath(nom)
    if nom.startswith('/') or ':' in nom or any(p in {'', '.', '..'} for p in nom.split('/')):
        raise ValueError('Un chemin relatif sans remontée de dossier est requis.')
    return chemin.as_posix()


def exclu(nom):
    parties = PurePosixPath(nom).parts
    return (any(p in EXCLUS or p.startswith('.') for p in parties)
            or parties[-1].lower() in NOMS_EXCLUS
            or PurePosixPath(nom).suffix.lower() not in ALLOWED_EXTENSIONS)


@dataclass
class FichierProjet:
    name: str
    data: bytes

    @property
    def size(self):
        return len(self.data)

    def getvalue(self):
        return self.data


@dataclass
class ImportProjet:
    fichiers: list = field(default_factory=list)
    ignores: list = field(default_factory=list)
    origine: str = ''


def fichiers_importes(fichiers, *, origine='Fichiers'):
    resultat = ImportProjet(origine=origine)
    noms, total = set(), 0
    if len(fichiers) > MAX_ENTRIES:
        raise ValueError('Trop d’entrées dans cette sélection.')
    for fichier in fichiers:
        nom = chemin_relatif(fichier.name)
        if exclu(nom):
            resultat.ignores.append((nom, 'format ou dossier exclu'))
            continue
        if nom.casefold() in noms:
            raise ValueError('Deux chemins identiques ou différents uniquement par la casse sont présents : ' + nom)
        noms.add(nom.casefold())
        if fichier.size > MAX_FILE_BYTES:
            resultat.ignores.append((nom, 'plus de 512 Ko'))
            continue
        donnees = fichier.getvalue()
        if len(donnees) > MAX_FILE_BYTES:
            raise ValueError('La taille déclarée d’un fichier est incorrecte.')
        if b'\x00' in donnees:
            resultat.ignores.append((nom, 'contenu binaire'))
            continue
        try:
            texte = donnees.decode('utf-8-sig')
        except UnicodeDecodeError:
            resultat.ignores.append((nom, 'encodage UTF-8 requis'))
            continue
        if not texte.strip():
            resultat.ignores.append((nom, 'fichier vide'))
            continue
        total += len(donnees)
        if len(resultat.fichiers) >= MAX_FILES or total > MAX_PROJECT_BYTES:
            raise ValueError('Projet trop volumineux : maximum 300 fichiers retenus et 20 Mo.')
        resultat.fichiers.append(FichierProjet(nom, donnees))
    return resultat


def lire_zip(donnees, *, origine='Archive ZIP'):
    if len(donnees) > MAX_ARCHIVE_BYTES:
        raise ValueError('Archive trop volumineuse : maximum 30 Mo compressés.')
    fichiers, ignores = [], []
    try:
        with zipfile.ZipFile(io.BytesIO(donnees)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > 60_000_000:
                raise ValueError('L’archive dépasse les limites d’entrées ou de décompression (60 Mo).')
            chemins = [(i, chemin_relatif(i.filename.rstrip('/'))) for i in infos if not i.is_dir()]
            # Retirer uniquement l’enveloppe commune de l’archive, pas les dossiers internes.
            racines = {n.split('/')[0] for _, n in chemins}
            enveloppe = len(racines) == 1 and all('/' in n for _, n in chemins)
            for info, chemin in chemins:
                nom = chemin.split('/', 1)[1] if enveloppe else chemin
                if stat.S_ISLNK(info.external_attr >> 16):
                    ignores.append((nom, 'lien symbolique non suivi'))
                    continue
                if info.flag_bits & 1:
                    raise ValueError('Les archives chiffrées ne sont pas prises en charge.')
                if exclu(chemin) or exclu(nom) or info.file_size > MAX_FILE_BYTES:
                    ignores.append((nom, 'format, dossier ou taille exclu'))
                    continue
                with archive.open(info) as flux:
                    contenu = flux.read(MAX_FILE_BYTES + 1)
                fichiers.append(FichierProjet(nom, contenu))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as erreur:
        raise ValueError('Archive ZIP invalide ou non prise en charge.') from erreur
    resultat = fichiers_importes(fichiers, origine=origine)
    resultat.ignores = ignores + resultat.ignores
    return resultat


def lire_github(url, reference='HEAD'):
    cible = urlsplit(url.strip())
    match = re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', cible.path)
    if cible.scheme != 'https' or cible.netloc != 'github.com' or cible.query or cible.fragment or not match:
        raise ValueError('Indiquez l’URL HTTPS d’un dépôt GitHub public, sans identifiants ni paramètres.')
    if not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}', reference) or '..' in reference:
        raise ValueError('Branche ou révision Git invalide.')
    owner, repo = match.groups()
    endpoint = f'https://codeload.github.com/{owner}/{repo}/zip/{quote(reference, safe="/")}'
    blocs, taille = [], 0
    debut = monotonic()
    with httpx.Client(timeout=20, follow_redirects=False) as client, client.stream('GET', endpoint) as reponse:
        if reponse.status_code != 200:
            raise ValueError('Dépôt ou révision inaccessible. Pour un dépôt privé, importez son archive ZIP.')
        for bloc in reponse.iter_bytes():
            if monotonic() - debut > 45:
                raise ValueError('Le téléchargement a dépassé le délai prévu.')
            taille += len(bloc)
            if taille > MAX_ARCHIVE_BYTES:
                raise ValueError('Le téléchargement dépasse la limite de 30 Mo.')
            blocs.append(bloc)
    return lire_zip(b''.join(blocs), origine=f'GitHub {owner}/{repo} · {reference}')


def arborescence(noms):
    racine = {}
    for nom in sorted(noms):
        courant = racine
        for partie in PurePosixPath(nom).parts:
            courant = courant.setdefault(partie, {})
    lignes = []

    def visiter(noeud, prefixe=''):
        for i, (nom, enfants) in enumerate(noeud.items()):
            dernier = i == len(noeud) - 1
            lignes.append(prefixe + ('└── ' if dernier else '├── ') + nom)
            visiter(enfants, prefixe + ('    ' if dernier else '│   '))

    visiter(racine)
    return '\n'.join(lignes)
