"""Connexion LangChain à Ollama Cloud ou à l'instance locale."""

from langchain_ollama import ChatOllama

from revisiter.config import Settings


def create_chat_model(settings: Settings) -> ChatOllama:
    headers = {}
    if settings.mode == "cloud":
        headers["Authorization"] = f"Bearer {settings.api_key}"
    return ChatOllama(
        model=settings.model,
        base_url=settings.base_url,
        temperature=0,
        client_kwargs={"headers": headers, "timeout": 60.0},
    )
