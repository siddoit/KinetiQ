"""
KINETIQ Lightweight Emotion Detector.

Uses ONNX Runtime (HSEmotion enet_b0_8_best_vgaf) + OpenCV Haar Cascade face detection.
Zero PyTorch dependency, sub-30ms inference on CPU.

Extracts facial emotion classes and maps them to empathetic wellness descriptors
for direct injection into the KINETIQ AI Mirror Assistant.
"""

from __future__ import annotations

import base64
import os
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

# Emotion mapping for empathetic wellness coaching
EMOTION_WELLNESS_MAP = {
    "Anger": {
        "descriptor": "tense and stressed",
        "label": "Tense / Stressed",
        "tone": "de-escalating, calming, recommend box breathing or a short break",
    },
    "Fear": {
        "descriptor": "anxious or overwhelmed",
        "label": "Anxious / Overwhelmed",
        "tone": "supportive, grounding, gentle pacing",
    },
    "Sadness": {
        "descriptor": "fatigued and low-energy",
        "label": "Fatigued / Low Energy",
        "tone": "encouraging, restorative, gentle recovery advice",
    },
    "Disgust": {
        "descriptor": "uncomfortable or strained",
        "label": "Strained",
        "tone": "check posture and physical comfort",
    },
    "Happiness": {
        "descriptor": "upbeat, happy, and energized",
        "label": "Upbeat / Energized",
        "tone": "positive, encouraging, maintain healthy momentum",
    },
    "Surprise": {
        "descriptor": "alert and engaged",
        "label": "Alert / Engaged",
        "tone": "informative, focused",
    },
    "Neutral": {
        "descriptor": "calm and balanced",
        "label": "Calm / Balanced",
        "tone": "steady, standard wellness pacing",
    },
    "Contempt": {
        "descriptor": "mildly tense",
        "label": "Mildly Tense",
        "tone": "relax shoulders, steady breathing",
    },
}


class EmotionDetector:
    """Singleton detector caching the HSEmotion ONNX session and Haar cascade."""

    _instance: Optional[EmotionDetector] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._cascade = None
        self._load_error: Optional[str] = None
        self._last_result: Dict[str, Any] = {
            "raw_emotion": "Neutral",
            "descriptor": "calm and balanced",
            "label": "Calm / Balanced",
            "confidence": 1.0,
            "face_detected": False,
            "timestamp": time.time(),
        }
        self._init_models()

    @classmethod
    def get_instance(cls) -> EmotionDetector:
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = EmotionDetector()
            return cls._instance

    def _init_models(self) -> None:
        try:
            from hsemotion_onnx.facial_emotions import HSEmotionRecognizer

            # Loads cached enet_b0_8_best_vgaf.onnx from ~/.hsemotion
            self._model = HSEmotionRecognizer(model_name="enet_b0_8_best_vgaf")
        except Exception as exc:
            self._load_error = f"HSEmotionRecognizer init failed: {exc}"
            print(f"[EmotionDetector] {self._load_error}", file=sys.stderr)

        try:
            cascade_path = os.path.join(
                cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
            )
            if os.path.exists(cascade_path):
                self._cascade = cv2.CascadeClassifier(cascade_path)
        except Exception as exc:
            print(f"[EmotionDetector] Cascade init warning: {exc}", file=sys.stderr)

    def detect_emotion_from_bgr(self, frame: np.ndarray) -> Dict[str, Any]:
        """Detect emotion from an OpenCV BGR frame in <30ms."""
        if frame is None or frame.size == 0 or self._model is None:
            return dict(self._last_result)

        h, w = frame.shape[:2]
        face_roi = None
        face_detected = False

        # 1. Try Haar face detection
        if self._cascade is not None and not self._cascade.empty():
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Scale down for ultra-fast face search
                faces = self._cascade.detectMultiScale(
                    gray, scaleFactor=1.15, minNeighbors=4, minSize=(40, 40)
                )
                if len(faces) > 0:
                    # Pick largest face
                    fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                    # Expand ROI slightly by 10%
                    pad_x = int(fw * 0.1)
                    pad_y = int(fh * 0.1)
                    x1 = max(0, fx - pad_x)
                    y1 = max(0, fy - pad_y)
                    x2 = min(w, fx + fw + pad_x)
                    y2 = min(h, fy + fh + pad_y)
                    face_roi = frame[y1:y2, x1:x2]
                    face_detected = True
            except Exception:
                face_roi = None

        # 2. Fallback: Center crop (mirror user is in center of frame)
        if face_roi is None or face_roi.size == 0 or face_roi.shape[0] < 12 or face_roi.shape[1] < 12:
            cy1 = max(0, int(h * 0.15))
            cy2 = min(h, int(h * 0.85))
            cx1 = max(0, int(w * 0.20))
            cx2 = min(w, int(w * 0.80))
            if cy2 > cy1 and cx2 > cx1:
                face_roi = frame[cy1:cy2, cx1:cx2]
            else:
                face_roi = frame

        if face_roi is None or face_roi.size == 0:
            return dict(self._last_result)

        # 3. ONNX HSEmotion prediction (EfficientNet expects RGB format)
        try:
            try:
                face_rgb = cv2.cvtColor(face_roi, cv2.COLOR_BGR2RGB)
            except Exception:
                face_rgb = face_roi

            with self._lock:
                raw_emo, scores = self._model.predict_emotions(
                    face_rgb, logits=False
                )
                conf = float(np.max(scores)) if scores is not None else 0.90

            mapping = EMOTION_WELLNESS_MAP.get(
                raw_emo,
                {
                    "descriptor": "calm and balanced",
                    "label": "Calm",
                    "tone": "balanced",
                },
            )

            result = {
                "raw_emotion": str(raw_emo),
                "descriptor": mapping["descriptor"],
                "label": mapping["label"],
                "tone": mapping.get("tone", "balanced"),
                "confidence": round(conf, 3),
                "face_detected": face_detected,
                "timestamp": time.time(),
            }
            self._last_result = result
            return result
        except Exception as exc:
            print(f"[EmotionDetector] Prediction failed: {exc}", file=sys.stderr)
            return dict(self._last_result)

    def detect_from_base64(self, data_url_or_b64: str) -> Dict[str, Any]:
        """Decode base64 JPEG from browser canvas and classify emotion."""
        if not data_url_or_b64:
            return dict(self._last_result)

        try:
            # Strip data URL prefix if present: "data:image/jpeg;base64,..."
            if "," in data_url_or_b64:
                b64_str = data_url_or_b64.split(",", 1)[1]
            else:
                b64_str = data_url_or_b64

            raw_bytes = base64.b64decode(b64_str)
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return dict(self._last_result)
            return self.detect_emotion_from_bgr(frame)
        except Exception as exc:
            print(f"[EmotionDetector] base64 decode failed: {exc}", file=sys.stderr)
            return dict(self._last_result)

    def get_latest(self) -> Dict[str, Any]:
        return dict(self._last_result)
