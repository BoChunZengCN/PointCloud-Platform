from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class EngineDescription:
    name: str
    version: str
    production: bool


@runtime_checkable
class RegistrationEngine(Protocol):
    def describe(self) -> EngineDescription:
        raise NotImplementedError

    def preprocess(
        self,
        model_points: np.ndarray,
        object_points: np.ndarray,
        config: dict,
    ) -> dict:
        raise NotImplementedError

    def coarse_register(
        self,
        prepared: dict,
        hypotheses: list[dict],
        config: dict,
    ) -> list[dict]:
        raise NotImplementedError

    def fine_register(
        self,
        prepared: dict,
        coarse_results: list[dict],
        config: dict,
    ) -> list[dict]:
        raise NotImplementedError

    def nearest_neighbor_evidence(
        self,
        prepared: dict,
        transform: np.ndarray,
        config: dict,
    ) -> dict:
        raise NotImplementedError
