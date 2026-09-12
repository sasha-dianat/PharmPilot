"""
Whisper-based transcription engine with pharmacy-specific vocabulary boosting.
Handles real-time streaming and batch transcription modes.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

# Pharmacy-specific vocabulary to boost in Whisper decoding
PHARMACY_VOCABULARY = [
    # Drug names (common — full list loaded from drug database at runtime)
    "metformin", "lisinopril", "atorvastatin", "amlodipine", "omeprazole",
    "metoprolol", "albuterol", "gabapentin", "sertraline", "escitalopram",
    "levothyroxine", "hydrochlorothiazide", "losartan", "pantoprazole",
    "montelukast", "rosuvastatin", "bupropion", "duloxetine", "tramadol",
    "cyclobenzaprine", "prednisone", "amoxicillin", "azithromycin",

    # Pharmacy terms
    "milligrams", "micrograms", "milliliters", "capsule", "tablet", "suspension",
    "inhaler", "injection", "suppository", "ointment", "topical", "sublingual",
    "twice daily", "three times daily", "four times daily", "as needed",
    "with food", "without food", "refill", "prescription", "copay",
    "prior authorization", "formulary", "generic", "brand name", "NDC",
    "days supply", "controlled substance", "Schedule II", "Schedule III",
    "DEA number", "NPI", "allergy", "adverse reaction", "side effect",
    "blood pressure", "blood sugar", "cholesterol", "thyroid", "diabetes",
    "hypertension", "heart failure", "kidney", "liver",

    # Clinical terms the AI enrichment module looks for
    "diagnosed with", "history of", "allergic to", "takes", "currently on",
    "stopped taking", "side effect from", "reaction to", "pregnant",
    "breastfeeding", "trying to conceive",
]


@dataclass
class TranscriptionSegment:
    start: float
    end: float
    text: str
    speaker_label: str = "SPEAKER_00"
    confidence: float = 0.0
    words: list[dict] = field(default_factory=list)


@dataclass
class TranscriptionResult:
    full_text: str
    segments: list[TranscriptionSegment]
    language: str
    duration_seconds: float
    transcription_confidence: float
    model_used: str


# `medium.en` was the default here. `.en` models are English-ONLY: on Persian
# they do not fail, they hallucinate plausible English. Persian requires a
# multilingual model, and the language is pinned rather than auto-detected
# because a short noisy counter utterance is exactly the case auto-detect gets
# wrong — and a wrong guess yields confident nonsense, not an error.
DEFAULT_MODEL = "large-v3"
DEFAULT_LANGUAGE = "fa"


class PharmacyTranscriptionEngine:
    """
    Whisper-based transcription with pharmacy vocabulary boosting.
    Supports two modes:
      - batch: full audio buffer → full transcript (for completed conversations)
      - streaming: chunk-by-chunk real-time transcription (for live counter display)
    """

    MODEL_SIZES = {
        # `.en` variants are ENGLISH-ONLY. They are listed because Whisper
        # offers them, not because they may be used here: on Persian they do not
        # fail, they emit plausible English that was never said.
        "tiny.en": {"speed": "fastest", "accuracy": "basic", "english_only": True},
        "base.en": {"speed": "fast", "accuracy": "good", "english_only": True},
        "medium.en": {"speed": "medium", "accuracy": "high", "english_only": True},
        "large-v3": {"speed": "slow", "accuracy": "highest", "english_only": False},
    }

    def __init__(self, model_size: str = DEFAULT_MODEL,
                 language: str = DEFAULT_LANGUAGE):
        self.model_size = model_size
        self.language = language
        self._model = None
        self._processor = None

    def _load_model(self):
        if self._model is None:
            import whisper
            logger.info("Loading Whisper model: %s", self.model_size)
            self._model = whisper.load_model(self.model_size)
            logger.info("Whisper model loaded")

    def transcribe_batch(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Transcribe a complete audio buffer.
        initial_prompt injects pharmacy vocabulary context into Whisper.
        """
        self._load_model()
        import whisper

        pharmacy_prompt = (
            initial_prompt
            or "Pharmacy conversation. Medical terms: "
            + ", ".join(PHARMACY_VOCABULARY[:50])
        )

        # None means "auto-detect" to Whisper, and auto-detect on a short noisy
        # counter utterance guesses wrong often enough to matter — producing
        # confident nonsense rather than an error. Fall back to the engine's
        # configured language instead.
        language = language or self.language

        start_time = time.time()
        result = self._model.transcribe(
            audio,
            language=language,
            initial_prompt=pharmacy_prompt,
            word_timestamps=True,
            condition_on_previous_text=True,
            temperature=0.0,        # Greedy decoding for accuracy
            best_of=1,
            beam_size=5,
            no_speech_threshold=0.6,
            logprob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        elapsed = time.time() - start_time

        segments = [
            TranscriptionSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip(),
                confidence=float(np.exp(seg.get("avg_logprob", -0.5))),
                words=seg.get("words", []),
            )
            for seg in result["segments"]
        ]

        overall_confidence = float(
            np.mean([s.confidence for s in segments]) if segments else 0.0
        )

        logger.info(
            "Transcription complete: %.1fs audio in %.1fs (%.1fx realtime), confidence=%.3f",
            audio.shape[0] / sample_rate,
            elapsed,
            (audio.shape[0] / sample_rate) / elapsed,
            overall_confidence,
        )

        return TranscriptionResult(
            full_text=result["text"].strip(),
            segments=segments,
            language=result.get("language", "en"),
            duration_seconds=audio.shape[0] / sample_rate,
            transcription_confidence=overall_confidence,
            model_used=self.model_size,
        )

    async def transcribe_streaming(
        self,
        audio_queue: asyncio.Queue,
        chunk_duration_seconds: float = 5.0,
        sample_rate: int = 16000,
    ) -> AsyncGenerator[TranscriptionSegment, None]:
        """
        Real-time streaming transcription.
        Yields TranscriptionSegment as each chunk is processed.
        Used for live display at pharmacist counter.
        """
        self._load_model()
        buffer = np.array([], dtype=np.float32)
        chunk_samples = int(chunk_duration_seconds * sample_rate)

        while True:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=2.0)
                if chunk is None:  # Sentinel — end of stream
                    break
                buffer = np.concatenate([buffer, chunk])

                while len(buffer) >= chunk_samples:
                    process_chunk = buffer[:chunk_samples]
                    buffer = buffer[chunk_samples:]

                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.transcribe_batch(process_chunk, sample_rate),
                    )

                    for seg in result.segments:
                        yield seg

            except asyncio.TimeoutError:
                # Process whatever is in the buffer
                if len(buffer) > sample_rate:  # At least 1 second
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.transcribe_batch(buffer, sample_rate),
                    )
                    buffer = np.array([], dtype=np.float32)
                    for seg in result.segments:
                        yield seg
