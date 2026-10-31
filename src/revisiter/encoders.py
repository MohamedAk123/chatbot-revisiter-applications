"""CamemBERT et BERT comme bases expérimentales de recherche de passages."""

from langchain_core.embeddings import Embeddings

MODEL_IDS = {
    "camembert": "almanach/camembert-base",
    "bert": "google-bert/bert-base-multilingual-cased",
}


class TransformerEmbeddings(Embeddings):
    """Moyenne masquée des représentations ; ce modèle n'est pas affiné pour le code.

    Les textes longs sont découpés en fenêtres de tokens puis agrégés.
    Le chargement et le téléchargement n'ont lieu qu'à la première utilisation.
    """

    def __init__(self, encoder: str = "camembert"):
        if encoder not in MODEL_IDS:
            raise ValueError("Encodeur attendu : camembert ou bert.")
        self.model_id = MODEL_IDS[encoder]
        self._model = self._tokenizer = self._torch = None

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as error:
            raise RuntimeError('Installez les dépendances NLP : pip install -e ".[nlp]"') from error
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=False)
        self._model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=False, use_safetensors=True
        ).eval()
        self._torch = torch

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._load()
        torch = self._torch
        results = []
        for text in texts:
            inputs = self._tokenizer(
                text, return_tensors="pt", padding=True, truncation=True,
                max_length=512, return_overflowing_tokens=True, stride=32,
                return_special_tokens_mask=True,
            )
            inputs.pop("overflow_to_sample_mapping", None)
            special = inputs.pop("special_tokens_mask")
            with torch.inference_mode():
                output = self._model(**inputs).last_hidden_state
                mask = (inputs["attention_mask"] * (1 - special)).unsqueeze(-1)
                pooled = (output * mask).sum(dim=(0, 1)) / mask.sum().clamp(min=1)
                vector = torch.nn.functional.normalize(pooled, dim=0)
            results.append(vector.tolist())
        return results

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
