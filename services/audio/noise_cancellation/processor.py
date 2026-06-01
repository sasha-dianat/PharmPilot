"""
Noise cancellation pipeline for pharmacy ambient audio.
Pharmacy-specific noise profile: pill counting machines, label printers,
phone ringing, drive-through intercoms, HVAC, and customer background chatter.
"""
import numpy as np
import noisereduce as nr
import webrtcvad
import soundfile as sf
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    data: np.ndarray
    sample_rate: int
    timestamp_ms: int
    contains_speech: bool = False
    noise_reduction_db: float = 0.0


class PharmacyNoiseCanceller:
    """
    Multi-stage noise cancellation optimized for pharmacy environments.

    Stage 1: WebRTC VAD — Voice Activity Detection, discard silent frames
    Stage 2: Spectral subtraction — remove stationary pharmacy noise
             (printer hum, HVAC, refrigeration units)
    Stage 3: noisereduce deep learning — non-stationary noise removal
             (pill counters, phone dings, background conversations)
    Stage 4: Bandpass filter — retain 300–3400 Hz (human speech band)
    Stage 5: Normalization — consistent volume for Whisper input
    """

    SAMPLE_RATE = 16000  # Whisper expects 16kHz
    FRAME_DURATION_MS = 30  # VAD frame size: 10, 20, or 30ms
    VAD_AGGRESSIVENESS = 2  # 0 (least aggressive) to 3 (most aggressive)

    # Pharmacy-specific noise frequency bands to suppress (Hz)
    PHARMACY_NOISE_BANDS = [
        (50, 120),    # Electrical hum, refrigeration compressors
        (3500, 8000), # High-frequency printer noise
    ]

    def __init__(self):
        self.vad = webrtcvad.Vad(self.VAD_AGGRESSIVENESS)
        self._noise_profile: Optional[np.ndarray] = None
        self._frames_for_noise_profile: list[np.ndarray] = []
        self._noise_profile_ready = False

    def update_noise_profile(self, silent_audio: np.ndarray) -> None:
        """
        Called during pharmacy quiet periods (before opening) to build
        a baseline noise profile specific to this pharmacy's environment.
        """
        self._frames_for_noise_profile.append(silent_audio)
        if len(self._frames_for_noise_profile) >= 10:
            combined = np.concatenate(self._frames_for_noise_profile)
            self._noise_profile = combined
            self._noise_profile_ready = True
            logger.info("Pharmacy noise profile calibrated from %d frames", len(self._frames_for_noise_profile))

    def process_chunk(self, audio_bytes: bytes, sample_rate: int = 16000) -> AudioChunk:
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Stage 1: Voice Activity Detection
        frame_bytes = audio_bytes
        is_speech = False
        try:
            is_speech = self.vad.is_speech(frame_bytes, sample_rate)
        except Exception:
            is_speech = True  # Fail open — don't discard audio if VAD errors

        if not is_speech:
            return AudioChunk(
                data=np.zeros_like(audio),
                sample_rate=sample_rate,
                timestamp_ms=0,
                contains_speech=False,
            )

        # Stage 2 + 3: Spectral noise reduction
        original_rms = float(np.sqrt(np.mean(audio ** 2)))

        if self._noise_profile_ready and self._noise_profile is not None:
            cleaned = nr.reduce_noise(
                y=audio,
                sr=sample_rate,
                y_noise=self._noise_profile,
                stationary=False,
                prop_decrease=0.85,
                time_constant_s=2.0,
            )
        else:
            # Stationary noise reduction without explicit profile
            cleaned = nr.reduce_noise(
                y=audio,
                sr=sample_rate,
                stationary=True,
                prop_decrease=0.75,
            )

        cleaned_rms = float(np.sqrt(np.mean(cleaned ** 2)))
        noise_reduction_db = (
            20 * np.log10(original_rms / (cleaned_rms + 1e-10))
            if cleaned_rms > 0 else 0.0
        )

        # Stage 4: Bandpass filter (300–3400 Hz speech band)
        cleaned = self._bandpass_filter(cleaned, sample_rate, low=300, high=3400)

        # Stage 5: Normalize
        peak = np.abs(cleaned).max()
        if peak > 0:
            cleaned = cleaned / peak * 0.95

        return AudioChunk(
            data=cleaned,
            sample_rate=sample_rate,
            timestamp_ms=0,
            contains_speech=True,
            noise_reduction_db=noise_reduction_db,
        )

    def _bandpass_filter(
        self,
        audio: np.ndarray,
        sr: int,
        low: float,
        high: float,
    ) -> np.ndarray:
        from scipy import signal
        nyq = sr / 2
        b, a = signal.butter(4, [low / nyq, high / nyq], btype="band")
        return signal.filtfilt(b, a, audio).astype(np.float32)

    def process_stream(self, audio_stream: bytes, sample_rate: int = 16000) -> bytes:
        """
        Process a full audio stream (e.g., a complete counter interaction).
        Returns cleaned audio ready for Whisper transcription.
        """
        audio = np.frombuffer(audio_stream, dtype=np.int16).astype(np.float32) / 32768.0
        frame_length = int(sample_rate * self.FRAME_DURATION_MS / 1000)
        cleaned_frames = []

        for i in range(0, len(audio) - frame_length, frame_length):
            frame = audio[i:i + frame_length]
            frame_bytes = (frame * 32768).astype(np.int16).tobytes()
            result = self.process_chunk(frame_bytes, sample_rate)
            if result.contains_speech:
                cleaned_frames.append(result.data)

        if not cleaned_frames:
            return b""

        cleaned_audio = np.concatenate(cleaned_frames)
        return (cleaned_audio * 32768).astype(np.int16).tobytes()
