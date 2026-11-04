import hashlib
import json
from io import BytesIO

import pytest

from revisiter import validation_isolee as validation


def preparation():
    avant, apres = 'def total(x):\n    return x\n', 'def total(x):\n    return x + 1\n'
    proposition = {'file': 'a.py', 'base_sha256': hashlib.sha256(avant.encode()).hexdigest(), 'code': apres}
    tests = {'file': 'tests/test_total.py', 'code': 'from a import total\ndef test_total():\n    assert total(1) == 2\n',
             'target': 'a.py', 'target_sha256': hashlib.sha256(apres.encode()).hexdigest()}
    return {'a.py': avant}, proposition, tests


def test_docker_absent_aucun_repli_sur_execution_hote(monkeypatch):
    monkeypatch.setattr(validation.shutil, 'which', lambda nom: None)
    monkeypatch.setattr(validation.subprocess, 'Popen', lambda *a, **k: pytest.fail('Aucun processus hôte'))
    rapport = validation.executer_validation(*preparation())
    assert rapport['executed'] is False and rapport['status'] == 'non exécuté'


@pytest.mark.parametrize('champ', ['original', 'tests', 'chemin', 'secret', 'masque', 'collision'])
def test_charge_incompatible_refusee_avant_execution(champ):
    documents, proposition, tests = preparation()
    if champ == 'original':
        documents['a.py'] = '# autre version'
    elif champ == 'tests':
        tests['target_sha256'] = 'invalide'
    elif champ == 'chemin':
        tests['file'] = '../test.py'
    elif champ == 'collision':
        tests['file'] = 'a.py'
    else:
        documents['config.py'] = 'password = "secret-fictif-long"' if champ == 'secret' else 'x="[SECRET_MASQUE]"'
    with pytest.raises(ValueError):
        validation.preparer_charge(documents, proposition, tests)


def test_commande_locale_sans_montage_reseau_et_avec_limites(monkeypatch):
    monkeypatch.setattr(validation.shutil, 'which', lambda n: 'docker')
    monkeypatch.setenv('DOCKER_HOST', 'tcp://externe.invalid:2375')
    commande = validation.commande_execution('revisiter-test-fictif')
    assert 'tcp://externe.invalid:2375' not in commande
    for option in ['--network=none', '--read-only', '--cap-drop=ALL', '--memory=512m', '--memory-swap=512m',
                   '--pids-limit=64', '--cpus=1', '--security-opt=no-new-privileges', '--pull=never']:
        assert option in commande
    assert not any(a.startswith(('--volume', '--mount', '--env=')) for a in commande)


@pytest.mark.parametrize('code,changes,statut', [(0, [], 'réussi'), (1, [], 'échoué'), (0, ['a.py'], 'échoué')])
def test_resultat_du_conteneur_et_modifications_des_entrees(monkeypatch, code, changes, statut):
    appels = []

    class Processus:
        def __init__(self, commande, **kwargs):
            appels.append(commande)
            self.stdin, self.stderr = BytesIO(), BytesIO()
            self.stdout = BytesIO(json.dumps({'returncode': code, 'output': 'résultat fictif', 'changed_inputs': changes}).encode())
            self.returncode = code

        def wait(self, timeout):
            return code

        def poll(self):
            return code

    monkeypatch.setattr(validation, 'diagnostic_isolation', lambda: {'ready': True})
    monkeypatch.setattr(validation, 'commande_docker', lambda: ['docker', '--host', 'unix:///var/run/docker.sock'])
    monkeypatch.setattr(validation.subprocess, 'Popen', Processus)
    monkeypatch.setattr(validation.subprocess, 'run', lambda cmd, **kw: appels.append(cmd))
    resultat = validation.executer_validation(*preparation())
    assert resultat['executed'] and resultat['status'] == statut
    assert appels[-1][-3:-1] == ['rm', '-f']
    assert appels[-1][-1].startswith('revisiter-test-')
