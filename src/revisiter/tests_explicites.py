"""Construire des tests Python depuis des cas JSON explicites, sans LLM ni exécution."""

import ast
import builtins
import json
import keyword
import re
from pathlib import PurePosixPath

from revisiter.transformations import empreinte


def _identifiant(nom):
    return isinstance(nom, str) and bool(re.fullmatch(r'[A-Za-z_]\w*', nom)) and not keyword.iskeyword(nom)


def construire_tests_explicites(transformation, cas_json, module):
    if not isinstance(cas_json, str) or len(cas_json) > 12000:
        raise ValueError('Les cas JSON doivent tenir dans 12 000 caractères.')
    if not isinstance(module, str) or not all(_identifiant(p) for p in module.split('.')):
        raise ValueError('Nom de module Python invalide.')
    try:
        cas = json.loads(cas_json)
        json.dumps(cas, allow_nan=False)
    except (ValueError, RecursionError) as erreur:
        raise ValueError('Les cas doivent être du JSON valide avec des nombres finis.') from erreur
    if not isinstance(cas, list) or not 2 <= len(cas) <= 10:
        raise ValueError('Décrivez entre deux et dix cas de test.')
    source = ast.parse(transformation['code'])
    fonctions = {n.name for n in source.body if isinstance(n, ast.FunctionDef)}
    variables = {n.id for instruction in source.body if isinstance(instruction, (ast.Assign, ast.AnnAssign))
                 for n in ast.walk(instruction) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    classes = {n.name for n in source.body if isinstance(n, ast.ClassDef)}
    code = ['import pytest', f'import {module} as target', '']
    for i, cas_test in enumerate(cas, 1):
        if not isinstance(cas_test, dict) or set(cas_test) - {'name', 'function', 'args', 'kwargs', 'setup', 'expected', 'raises', 'after'}:
            raise ValueError('Champs permis : name, function, args, kwargs, setup, expected ou raises, after.')
        fonction = cas_test.get('function')
        if fonction not in fonctions:
            raise ValueError('Une fonction de test cible une fonction globale absente du fichier.')
        if ('expected' in cas_test) == ('raises' in cas_test):
            raise ValueError('Chaque cas doit préciser exactement expected ou raises.')
        arguments, mots = cas_test.get('args', []), cas_test.get('kwargs', {})
        setup, apres = cas_test.get('setup', {}), cas_test.get('after', {})
        if not isinstance(arguments, list) or not isinstance(mots, dict) or any(not _identifiant(k) for k in mots):
            raise ValueError('args doit être une liste ; kwargs, un objet de paramètres nommés.')
        if not isinstance(setup, dict) or not isinstance(apres, dict) or any(k not in variables for k in [*setup, *apres]):
            raise ValueError('setup et after doivent cibler des variables globales déclarées dans le fichier.')
        nom = re.sub(r'\W+', '_', str(cas_test.get('name', 'cas')))[:50]
        code.append(f'def test_{i}_{nom}():')
        for cle, valeur in setup.items():
            code.append(f'    target.{cle} = {valeur!r}')
        params = [repr(v) for v in arguments] + [k + '=' + repr(v) for k, v in mots.items()]
        appel = f'target.{fonction}(' + ', '.join(params) + ')'
        if 'raises' in cas_test:
            exception = cas_test['raises']
            if not _identifiant(exception):
                raise ValueError('Nom d’exception invalide.')
            type_exception = getattr(builtins, exception, None)
            if isinstance(type_exception, type) and issubclass(type_exception, Exception):
                ref_exception = exception
            elif exception in classes:
                ref_exception = 'target.' + exception
            else:
                raise ValueError('Exception absente des exceptions standard et du fichier ciblé.')
            code += [f'    with pytest.raises({ref_exception}):', '        ' + appel]
        else:
            code.append(f'    assert {appel} == {cas_test["expected"]!r}')
        for cle, valeur in apres.items():
            code.append(f'    assert target.{cle} == {valeur!r}')
        code.append('')
    texte = '\n'.join(code)
    ast.parse(texte)
    return {'file': 'test_' + PurePosixPath(transformation['file']).stem + '.py', 'target': transformation['file'],
        'target_sha256': empreinte(transformation['code']), 'code': texte, 'criteria': cas_json,
        'framework': 'pytest · cas explicites', 'syntax': 'ok', 'executed': False, 'origin': 'explicit_cases'}
