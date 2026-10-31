"""Configuration sans clé inscrite dans le code."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mode: str
    model: str
    api_key: str = field(default="", repr=False)
    encoder: str = "camembert"

    def __post_init__(self):
        if self.mode not in {"cloud", "local"}:
            raise ValueError("OLLAMA_MODE doit être cloud ou local.")
        if not self.model.strip():
            raise ValueError("Renseignez OLLAMA_MODEL dans le fichier .env.")
        if self.mode == "cloud" and not self.api_key.strip():
            raise ValueError("Renseignez OLLAMA_API_KEY dans le fichier .env pour Ollama Cloud.")
        if self.encoder not in {"camembert", "bert"}:
            raise ValueError("NLP_ENCODER doit être camembert ou bert.")

    @property
    def base_url(self) -> str:
        return "https://ollama.com" if self.mode == "cloud" else "http://localhost:11434"


def load_settings() -> Settings:
    # Chargement explicite depuis le dossier de lancement, sans modifier l'environnement.
    load_dotenv(".env", override=False)
    return Settings(
        mode=os.getenv("OLLAMA_MODE", "cloud").strip(),
        model=os.getenv("OLLAMA_MODEL", "").strip(),
        api_key=os.getenv("OLLAMA_API_KEY", "").strip(),
        encoder=os.getenv("NLP_ENCODER", "camembert").strip(),
    )
