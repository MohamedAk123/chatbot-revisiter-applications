"""Connexion LangChain à Ollama Cloud ou à l'instance locale."""

from langchain_ollama import ChatOllama

from revisiter.config import Settings
from revisiter.moteur_generation import planifier_modele


def create_chat_model(settings: Settings, *, context_window: int | None = None) -> ChatOllama:
    headers = {}
    if settings.mode == "cloud":
        headers["Authorization"] = f"Bearer {settings.api_key}"
    options = {"keep_alive": "15m"} if settings.mode == "local" else {}
    if context_window is not None:
        options["num_ctx"] = context_window
        options["num_predict"] = 2048
    client = {"headers": headers, "timeout": 90.0}
    if settings.mode == "local":
        client["trust_env"] = False
    return planifier_modele(ChatOllama(
        model=settings.model,
        base_url=settings.base_url,
        temperature=0,
        **options,
        client_kwargs=client,
    ))
