"""Point d'entrée pour une première question sur des fichiers explicitement choisis."""

import argparse
from pathlib import Path

from revisiter.config import load_settings
from revisiter.encoders import TransformerEmbeddings
from revisiter.ingestion import read_project
from revisiter.ollama_client import create_chat_model
from revisiter.workflow import build_question_graph


def main():
    parser = argparse.ArgumentParser(description="Comprendre un petit projet avec ses sources.")
    parser.add_argument("files", type=Path, nargs="+", help="Fichiers textuels explicitement choisis")
    parser.add_argument("--question", required=True, help="Question sur le projet")
    args = parser.parse_args()
    if not args.question.strip():
        parser.error("La question ne peut pas être vide.")
    try:
        settings = load_settings()
        chunks = read_project(args.files)
        graph = build_question_graph(
            chunks, TransformerEmbeddings(settings.encoder), create_chat_model(settings)
        )
        result = graph.invoke({"question": args.question})
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Erreur : {error}\n")
    except Exception:  # noqa: BLE001 -- frontière CLI : ne pas afficher les secrets d'un client HTTP
        # Ne pas exposer les en-têtes HTTP ou le contenu des fichiers dans une trace.
        parser.exit(1, "Échec du service IA ou NLP. Vérifiez le modèle, la clé et la connexion.\n")
    print(result["answer"])
    print("\nPassages fournis au modèle (pertinence et citations à évaluer) :")
    for source in result["sources"]:
        print(f"- {source}")


if __name__ == "__main__":
    main()
