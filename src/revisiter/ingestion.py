"""Lecture de fichiers textuels explicites. Aucun code importé n'est exécuté."""

from dataclasses import dataclass
from pathlib import Path

ALLOWED_EXTENSIONS = {
    # Python et notebooks Jupyter
    ".py", ".pyi", ".ipynb",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",

    # Interface web
    ".html", ".htm", ".css", ".scss", ".sass",
    ".vue", ".svelte",

    # Autres langages
    ".java", ".kt", ".kts", ".cs",
    ".c", ".h", ".cpp", ".hpp", ".cc",
    ".go", ".rs", ".php", ".rb",
    ".swift", ".dart", ".scala",

    # Scripts
    ".sh", ".ps1", ".bat",

    # Configuration et données textuelles
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".ini", ".cfg", ".properties",
    ".sql", ".graphql", ".gql", ".csv", ".tsv",

    # Documentation
    ".md", ".mdx", ".txt", ".rst",
}
EXCLUDED_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
MAX_FILE_BYTES = 200_000
MAX_PROJECT_BYTES = 2_000_000
MAX_FILES = 50


@dataclass(frozen=True)
class Chunk:
    source: str
    start_line: int
    end_line: int
    text: str

    @property
    def reference(self) -> str:
        return f"{self.source}:L{self.start_line}-L{self.end_line}"


def read_project(paths: list[Path], lines_per_chunk: int = 30) -> list[Chunk]:
    if not paths or len(paths) > MAX_FILES:
        raise ValueError(f"Sélectionnez entre 1 et {MAX_FILES} fichiers.")
    if lines_per_chunk < 1:
        raise ValueError("La taille des passages doit être positive.")
    chunks, seen, total = [], set(), 0
    for path in paths:
        if path.is_symlink() or EXCLUDED_PARTS.intersection(path.parts):
            raise ValueError(f"Fichier ou dossier exclu : {path.name}")
        if path.suffix.lower() not in ALLOWED_EXTENSIONS or path.name.startswith("."):
            raise ValueError(f"Format non accepté : {path.name}")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"Un fichier est attendu : {path.name}")
        if resolved in seen:
            continue
        seen.add(resolved)
        # La lecture est bornée même si le fichier change pendant la lecture.
        with resolved.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_PROJECT_BYTES:
            raise ValueError("Limite dépassée : 200 Ko par fichier et 2 Mo par projet.")
        if b"\x00" in raw:
            raise ValueError(f"Contenu binaire rejeté : {path.name}")
        try:
            lines = raw.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as error:
            raise ValueError(f"Encodage UTF-8 requis : {path.name}") from error
        # Conserver le chemin fourni pour distinguer deux fichiers de même nom.
        source = path.as_posix()
        for start in range(0, len(lines), lines_per_chunk):
            block = lines[start : start + lines_per_chunk]
            chunks.append(Chunk(source, start + 1, start + len(block), "\n".join(block)))
    if not chunks:
        raise ValueError("Les fichiers sélectionnés ne contiennent aucun texte.")
    return chunks
