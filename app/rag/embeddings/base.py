"""Abstract embedding provider interface."""
from abc import ABC, abstractmethod
from langchain_core.embeddings import Embeddings


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    def get_embeddings(self) -> Embeddings:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
