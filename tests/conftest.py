"""Isoler les tests des comptes et données configurés sur le poste du développeur."""

import pytest


@pytest.fixture(autouse=True)
def donnees_de_test_isolees(monkeypatch, tmp_path):
    monkeypatch.setenv('REVISITER_AUTH_ENABLED', 'false')
    monkeypatch.setenv('REVISITER_DATA_DIR', str(tmp_path / 'atelier_test'))
    yield
    from revisiter.executions import gestionnaire_executions
    if gestionnaire_executions.cache_info().currsize:
        gestionnaire_executions().fermer()
        gestionnaire_executions.cache_clear()
