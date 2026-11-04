"""Validation dans un conteneur local jetable, jamais dans le Python hôte."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Thread
from uuid import uuid4

from revisiter.projets import chemin_relatif
from revisiter.securite_analyse import MARQUEUR, examiner_fichier

IMAGE = 'revisiter-validation:1'
DELAI = 45
HARNAIS = r'''import hashlib, json, os, pathlib, subprocess, sys
payload = json.loads(sys.stdin.buffer.read(22_000_001))
root = pathlib.Path('/work')
for name, text in payload['documents'].items():
    p = pathlib.PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or ':' in name or not name:
        raise ValueError('path')
    dest = root / p
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')
test = root / payload['test_file']
test.parent.mkdir(parents=True, exist_ok=True)
test.write_text(payload['test_code'], encoding='utf-8')
before = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='/work:/work/src', HOME='/work')
cmd = [sys.executable, '-m', 'pytest', '-q', '-c', '/opt/pytest.ini', '--rootdir=/work', '-p', 'no:cacheprovider', payload['test_file']]
if payload['language'] == 'javascript':
    cmd = ['node', '--test', payload['test_file']]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
tail = bytearray()
while True:
    block = p.stdout.read(4096)
    if not block:
        break
    tail.extend(block)
    if len(tail) > 12000:
        del tail[:-12000]
code = p.wait()
changed = [n for n, digest in before.items() if not (root/n).is_file() or hashlib.sha256((root/n).read_bytes()).hexdigest() != digest]
print(json.dumps({'returncode': code, 'output': tail.decode('utf-8', errors='replace'), 'changed_inputs': changed}), flush=True)
sys.exit(0 if code == 0 and not changed else 1)
'''
DOCKERFILE = '''FROM python:3.11-slim-bookworm
LABEL org.revisiter.validation="1"
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir pytest==8.4.2
COPY verifier.py /opt/verifier.py
COPY pytest.ini /opt/pytest.ini
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python", "-I", "/opt/verifier.py"]
'''


def commande_docker():
    docker = shutil.which('docker')
    if not docker:
        return None
    endpoint = 'npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
    return [docker, '--host', endpoint]


def diagnostic_isolation():
    commande = commande_docker()
    if not commande:
        return {'ready': False, 'reason': 'Docker local est absent. Les tests dynamiques ne sont pas exécutés.'}
    try:
        info = subprocess.run(commande + ['info', '--format', '{{json .}}'], capture_output=True, timeout=5, check=True)
        capacites = json.loads(info.stdout)
        if capacites.get('OSType') != 'linux' or not all(capacites.get(n) for n in ('MemoryLimit', 'PidsLimit', 'CpuCfsQuota')):
            return {'ready': False, 'reason': 'Le moteur doit proposer des conteneurs Linux et les limites mémoire, processus et CPU.'}
        image = subprocess.run(commande + ['image', 'inspect', IMAGE, '--format', '{{json .Config.Labels}}'],
                               capture_output=True, timeout=5, check=True)
        if json.loads(image.stdout).get('org.revisiter.validation') != '1':
            return {'ready': False, 'reason': 'L’image locale de validation n’est pas reconnue.'}
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return {'ready': False, 'reason': 'Docker local ou l’image de validation est indisponible. Préparez l’image avant les tests.'}
    return {'ready': True, 'reason': 'Moteur local et image disponibles. Exécution réseau coupé, sans montage du poste.'}


def preparer_charge(documents, transformation, tests):
    if not isinstance(documents, dict) or len(documents) > 300:
        raise ValueError('Projet de test invalide.')
    cible = chemin_relatif(transformation['file'])
    if cible not in documents or hashlib.sha256(documents[cible].encode()).hexdigest() != transformation['base_sha256']:
        raise ValueError('La version du fichier a changé depuis la proposition.')
    code = transformation['code']
    if tests.get('target') != cible or tests.get('target_sha256') != hashlib.sha256(code.encode()).hexdigest():
        raise ValueError('Ces tests ne correspondent pas au fichier proposé.')
    extension = Path(cible).suffix.lower()
    langage = 'python' if extension == '.py' else 'javascript' if extension in {'.js', '.mjs', '.cjs'} else None
    if langage is None:
        raise ValueError('Exécution disponible pour Python/pytest et JavaScript/node:test ; autres formats non exécutés.')
    chemin_test = chemin_relatif(tests['file'])
    if chemin_test in documents:
        raise ValueError('Le fichier de tests proposé remplacerait un fichier existant. Choisissez un nom distinct.')
    projet = {**documents, cible: code}
    for nom, contenu in {**projet, chemin_test: tests['code']}.items():
        chemin_relatif(nom)
        if not isinstance(contenu, str) or len(contenu.encode()) > 600_000:
            raise ValueError('Fichier invalide ou trop volumineux pour la validation.')
        examen = examiner_fichier(nom, contenu)
        if examen['secret_count'] or MARQUEUR in contenu:
            raise ValueError('Retirez les secrets et valeurs masquées du jeu de validation avant son exécution.')
    charge = {'documents': projet, 'test_file': chemin_test, 'test_code': tests['code'], 'language': langage}
    donnees = json.dumps(charge, ensure_ascii=False).encode()
    if len(donnees) > 22_000_000:
        raise ValueError('Le jeu de validation dépasse 22 Mo.')
    return donnees


def commande_execution(nom):
    return commande_docker() + ['run', '--name', nom, '--rm', '--pull=never', '-i',
        '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--user=65534:65534', '--pids-limit=64', '--cpus=1', '--memory=512m', '--memory-swap=512m',
        '--ulimit=nofile=128:128', '--log-driver=none',
        '--tmpfs=/work:rw,noexec,nosuid,nodev,size=67108864,mode=1777', '--workdir=/work', IMAGE]


def executer_validation(documents, transformation, tests):
    charge = preparer_charge(documents, transformation, tests)
    signature = hashlib.sha256((transformation['code'] + '\0' + tests['code']).encode()).hexdigest()
    diagnostic = diagnostic_isolation()
    if not diagnostic['ready']:
        return {'status': 'non exécuté', 'executed': False, 'reason': diagnostic['reason'], 'validation_sha256': signature}
    nom = 'revisiter-test-' + uuid4().hex
    sortie, erreurs = bytearray(), bytearray()

    def lire(flux, tampon):
        try:
            for bloc in iter(lambda: flux.read(4096), b''):
                tampon.extend(bloc)
                if len(tampon) > 16_000:
                    del tampon[:-16_000]
        except (OSError, ValueError):
            return

    processus = None
    try:
        processus = subprocess.Popen(commande_execution(nom), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lecteurs = [Thread(target=lire, args=(processus.stdout, sortie), daemon=True),
                    Thread(target=lire, args=(processus.stderr, erreurs), daemon=True)]
        for lecteur in lecteurs:
            lecteur.start()

        def ecrire():
            try:
                processus.stdin.write(charge)
                processus.stdin.close()
            except (OSError, ValueError):
                pass

        auteur = Thread(target=ecrire, daemon=True)
        auteur.start()
        processus.wait(timeout=DELAI)
        for lecteur in lecteurs:
            lecteur.join(timeout=2)
        try:
            resultat = json.loads(sortie.decode('utf-8'))
            if not isinstance(resultat, dict) or not isinstance(resultat.get('returncode'), int) or not isinstance(resultat.get('changed_inputs'), list):
                raise TypeError('Rapport invalide.')
        except (ValueError, TypeError, UnicodeError):
            return {'status': 'erreur outillage', 'executed': True, 'reason': 'Le conteneur n’a pas produit de rapport valide.', 'validation_sha256': signature}
        journal = examiner_fichier('journal', str(resultat.get('output', ''))[:12_000])['text']
        succes = processus.returncode == resultat.get('returncode') == 0 and not resultat.get('changed_inputs')
        return {'status': 'réussi' if succes else 'échoué', 'executed': True, 'output': journal,
                'returncode': resultat.get('returncode'), 'changed_inputs': resultat.get('changed_inputs', []),
                'target_sha256': tests['target_sha256'], 'validation_sha256': signature, 'isolation': 'Docker local, réseau coupé, aucun montage hôte',
                'limits': 'Tests exécutés sur une copie jetable. Leur réussite ne prouve pas tous les comportements.'}
    except subprocess.TimeoutExpired:
        return {'status': 'délai dépassé', 'executed': True, 'reason': 'Le conteneur a dépassé 45 secondes et a été arrêté.', 'validation_sha256': signature}
    except OSError:
        return {'status': 'erreur outillage', 'executed': False, 'reason': 'Impossible de démarrer le moteur local.', 'validation_sha256': signature}
    finally:
        if processus is not None and processus.poll() is None:
            try:
                processus.kill()
                processus.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            subprocess.run(commande_docker() + ['rm', '-f', nom], capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def main():
    parser = argparse.ArgumentParser(description='Préparer l’image locale de validation de Revisiter.')
    parser.add_argument('--preparer', action='store_true', required=True)
    parser.parse_args()
    commande = commande_docker()
    if commande is None:
        raise SystemExit('Docker local est absent. Installez et démarrez Docker Desktop avec les conteneurs Linux.')
    with tempfile.TemporaryDirectory(prefix='revisiter-image-') as temporaire:
        dossier = Path(temporaire)
        (dossier / 'Dockerfile').write_text(DOCKERFILE, encoding='utf-8')
        (dossier / 'verifier.py').write_text(HARNAIS, encoding='utf-8')
        (dossier / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        subprocess.run(commande + ['build', '--tag', IMAGE, str(dossier)], check=True, timeout=600)
    print('Image locale prête. Les validations utiliseront cette image sans réseau et sans montage hôte.')


if __name__ == '__main__':
    main()
