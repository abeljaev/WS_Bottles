"""Vision модуль: камера, инференс, классификация."""

from vision.camera_manager import CameraManager
from vision.inference_engine import InferenceEngine
from vision.inference_service import InferenceClient

__all__ = ["CameraManager", "InferenceEngine", "InferenceClient"]
