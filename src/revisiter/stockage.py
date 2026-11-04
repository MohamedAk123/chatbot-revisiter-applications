"""Persistance SQLite et contrôle d’accès côté serveur, sans pickle ni code exécutable."""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600


class AccesRefuse(ValueError):
    pass


class ConflitVersion(ValueError):
    pass


def _mot_de_passe(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS).hex()


def _compte_valide(username, password):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}', username):
        raise ValueError('Identifiant : 3 à 40 lettres, chiffres, points, tirets ou traits de soulignement.')
    if not isinstance(password, str) or not 15 <= len(password) <= 256:
        raise ValueError('Le mot de passe doit contenir entre 15 et 256 caractères.')


class Stockage:
    def __init__(self, dossier=None, *, partage=None):
        self.partage = (os.getenv('REVISITER_AUTH_ENABLED', '').lower() == 'true') if partage is None else partage
        dossier = Path(dossier or os.getenv('REVISITER_DATA_DIR', '.revisiter')).resolve()
        dossier.mkdir(parents=True, exist_ok=True)
        self.chemin = dossier / 'atelier.sqlite3'
        with self.connexion() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    salt TEXT, password_hash TEXT, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    failures INTEGER NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO users(id,username,role) VALUES(0,'personnel','local');
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,owner INTEGER NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL,payload TEXT NOT NULL,version INTEGER NOT NULL,updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members (project_id TEXT NOT NULL REFERENCES projects(id),user_id INTEGER NOT NULL REFERENCES users(id),
                    role TEXT NOT NULL,PRIMARY KEY(project_id,user_id));
                CREATE TABLE IF NOT EXISTS caches (user_id INTEGER NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL,cache_key TEXT NOT NULL,value TEXT NOT NULL,
                    PRIMARY KEY(user_id,project_id,kind,cache_key));
            ''')

    @contextmanager
    def connexion(self):
        db = sqlite3.connect(self.chemin, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def administrateur_present(self):
        with self.connexion() as db:
            return bool(db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone())

    def initialiser_admin(self, username, password):
        _compte_valide(username, password)
        salt = secrets.token_bytes(16)
        password_hash = _mot_de_passe(password, salt)
        with self.connexion() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise AccesRefuse('Un administrateur existe déjà.')
            db.execute("INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,'admin')",
                (username.casefold(), salt.hex(), password_hash))

    def connexion_utilisateur(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 40 or len(password) > 256:
            raise AccesRefuse('Identifiants invalides.')
        maintenant = time.time()
        with self.connexion() as db:
            row = db.execute('SELECT * FROM users WHERE username=? AND id<>0', (username.casefold(),)).fetchone()
        salt = bytes.fromhex(row['salt']) if row and row['salt'] else b'\x00' * 16
        candidat = _mot_de_passe(password, salt)
        valide = row and row['active'] and row['blocked_until'] <= maintenant and hmac.compare_digest(candidat, row['password_hash'])
        with self.connexion() as db:
            if not valide:
                if row:
                    db.execute('UPDATE users SET failures=failures+1,blocked_until=CASE WHEN failures+1>=5 THEN ? ELSE blocked_until END WHERE id=?',
                        (maintenant + 300, row['id']))
                    db.commit()
                raise AccesRefuse('Identifiants invalides ou accès temporairement suspendu.')
            db.execute('UPDATE users SET failures=0,blocked_until=0 WHERE id=?', (row['id'],))
            db.execute('DELETE FROM sessions WHERE expires<?', (maintenant,))
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), row['id'], maintenant + SESSION_SECONDS))
        return token

    def _principal(self, db, token):
        if not self.partage:
            return dict(db.execute('SELECT * FROM users WHERE id=0').fetchone())
        if not isinstance(token, str):
            raise AccesRefuse('Connectez-vous pour ouvrir cet espace.')
        row = db.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1',
            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise AccesRefuse('Session expirée ou accès retiré. Reconnectez-vous.')
        return dict(row)

    def utilisateur(self, token=None):
        with self.connexion() as db:
            row = self._principal(db, token)
            return {k: row[k] for k in ('id', 'username', 'role')}

    def deconnecter(self, token):
        if token:
            with self.connexion() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def creer_utilisateur(self, token, username, password, role='membre'):
        _compte_valide(username, password)
        if role not in {'admin', 'membre'}:
            raise ValueError('Rôle invalide.')
        salt = secrets.token_bytes(16)
        password_hash = _mot_de_passe(password, salt)
        with self.connexion() as db:
            if self._principal(db, token)['role'] != 'admin':
                raise AccesRefuse('Seul un administrateur peut créer un compte.')
            try:
                db.execute('INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,?)',
                    (username.casefold(), salt.hex(), password_hash, role))
            except sqlite3.IntegrityError as erreur:
                raise ValueError('Cet identifiant est déjà utilisé.') from erreur

    def liste_utilisateurs(self, token):
        with self.connexion() as db:
            if self._principal(db, token)['role'] != 'admin':
                raise AccesRefuse('Accès administrateur requis.')
            return [dict(r) for r in db.execute('SELECT id,username,role,active FROM users WHERE id<>0')]

    def _acces(self, db, token, projet, ecriture=False, proprietaire=False):
        user = self._principal(db, token)
        row = db.execute('SELECT p.id,p.owner,p.name,p.version,p.updated,m.role AS member_role FROM projects p LEFT JOIN members m ON m.project_id=p.id AND m.user_id=? WHERE p.id=?',
            (user['id'], projet)).fetchone()
        if not row or (row['owner'] != user['id'] and row['member_role'] not in {'lecture', 'edition'}):
            raise AccesRefuse('Espace inaccessible.')
        if proprietaire and row['owner'] != user['id']:
            raise AccesRefuse('Seul le propriétaire peut gérer les accès de cet espace.')
        if ecriture and row['owner'] != user['id'] and row['member_role'] != 'edition':
            raise AccesRefuse('Cet espace est accessible en lecture uniquement.')
        return user, row

    def lister_projets(self, token=None):
        with self.connexion() as db:
            user = self._principal(db, token)
            rows = db.execute('SELECT DISTINCT p.id,p.name,p.version,p.updated,p.owner,m.role AS member_role FROM projects p LEFT JOIN members m ON m.project_id=p.id AND m.user_id=? WHERE p.owner=? OR m.role IN (?,?) ORDER BY p.updated DESC',
                (user['id'], user['id'], 'lecture', 'edition')).fetchall()
            return [dict(r) for r in rows]

    def ouvrir_projet(self, projet, token=None):
        with self.connexion() as db:
            user, row = self._acces(db, token, projet)
            return {'id': row['id'], 'name': row['name'], 'version': row['version'],
                'owner': row['owner'], 'can_edit': row['owner'] == user['id'] or row['member_role'] == 'edition',
                'snapshot': json.loads(db.execute('SELECT payload FROM projects WHERE id=?', (projet,)).fetchone()[0])}

    def autorisation_projet(self, projet, token=None):
        with self.connexion() as db:
            user, row = self._acces(db, token, projet)
            return {'owner': row['owner'] == user['id'],
                    'can_edit': row['owner'] == user['id'] or row['member_role'] == 'edition', 'version': row['version']}

    def enregistrer_projet(self, nom, snapshot, *, projet=None, version=None, token=None):
        if not isinstance(nom, str) or not 1 <= len(nom.strip()) <= 120:
            raise ValueError('Donnez un nom de projet (120 caractères maximum).')
        payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 40_000_000:
            raise ValueError('La sauvegarde dépasse la limite de 40 Mo.')
        maintenant = datetime.now(UTC).isoformat()
        with self.connexion() as db:
            db.execute('BEGIN IMMEDIATE')
            user = self._principal(db, token)
            if projet:
                _, row = self._acces(db, token, projet, ecriture=True)
                if version != row['version']:
                    raise ConflitVersion('Cet espace a été modifié ailleurs. Rechargez-le ou enregistrez une copie.')
                nouveau = row['version'] + 1
                db.execute('UPDATE projects SET name=?,payload=?,version=?,updated=? WHERE id=?',
                    (nom.strip(), payload, nouveau, maintenant, projet))
            else:
                projet, nouveau = str(uuid4()), 1
                db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (projet, user['id'], nom.strip(), payload, nouveau, maintenant))
        return projet, nouveau

    def partager(self, projet, username, role, token=None):
        if role not in {'lecture', 'edition', 'retirer'}:
            raise ValueError('Rôle de partage invalide.')
        with self.connexion() as db:
            _, espace = self._acces(db, token, projet, proprietaire=True)
            user = db.execute('SELECT id FROM users WHERE username=? AND active=1 AND id<>0', (username.casefold(),)).fetchone()
            if not user or user['id'] == espace['owner']:
                raise ValueError('Choisissez un autre compte actif.')
            if role == 'retirer':
                db.execute('DELETE FROM members WHERE project_id=? AND user_id=?', (projet, user['id']))
                db.execute('DELETE FROM caches WHERE project_id=? AND user_id=?', (projet, user['id']))
            else:
                db.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role', (projet, user['id'], role))

    def lire_cache(self, projet, kind, cle, token=None):
        return self.lire_caches(projet, kind, [cle], token).get(cle)

    def lire_caches(self, projet, kind, cles, token=None):
        resultat = {}
        with self.connexion() as db:
            user, _ = self._acces(db, token, projet)
            for debut in range(0, len(cles), 400):
                partie = cles[debut:debut + 400]
                places = ','.join('?' for _ in partie)
                rows = db.execute('SELECT cache_key,value FROM caches WHERE user_id=? AND project_id=? AND kind=? AND cache_key IN (' + places + ')',
                    [user['id'], projet, kind, *partie]).fetchall()
                for row in rows:
                    try:
                        resultat[row['cache_key']] = json.loads(row['value'])
                    except (ValueError, TypeError):
                        continue
        return resultat

    def ecrire_cache(self, projet, kind, cle, valeur, token=None):
        self.ecrire_caches(projet, kind, {cle: valeur}, token)

    def ecrire_caches(self, projet, kind, valeurs, token=None):
        lignes = [(cle, json.dumps(valeur, allow_nan=False, ensure_ascii=False)) for cle, valeur in valeurs.items()]
        with self.connexion() as db:
            user, _ = self._acces(db, token, projet)
            db.executemany('INSERT INTO caches VALUES(?,?,?,?,?) ON CONFLICT(user_id,project_id,kind,cache_key) DO UPDATE SET value=excluded.value',
                [(user['id'], projet, kind, cle, payload) for cle, payload in lignes if len(payload) <= 2_000_000])
            db.execute('DELETE FROM caches WHERE rowid IN (SELECT rowid FROM caches WHERE user_id=? AND project_id=? AND kind=? ORDER BY rowid DESC LIMIT -1 OFFSET 5000)',
                (user['id'], projet, kind))

    def purger_cache(self, projet, token=None):
        with self.connexion() as db:
            user, _ = self._acces(db, token, projet)
            db.execute('DELETE FROM caches WHERE user_id=? AND project_id=?', (user['id'], projet))
