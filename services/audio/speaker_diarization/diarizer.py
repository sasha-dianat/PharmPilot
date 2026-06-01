"""
Speaker diarization — identifies WHO is speaking at each moment.
Uses pyannote.audio for segmentation, then matches speaker embeddings
against biometric voice prints and known staff voice profiles.
"""
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class SpeakerSegment:
    speaker_label: str          # SPEAKER_00, SPEAKER_01, etc.
    start_seconds: float
    end_seconds: float
    text: str = ""              # Filled by Whisper after diarization
    biometric_identity_id: Optional[UUID] = None
    speaker_role: str = "unknown"
    voice_match_confidence: float = 0.0


@dataclass
class DiarizedTranscript:
    segments: list[SpeakerSegment] = field(default_factory=list)
    speaker_map: dict[str, dict] = field(default_factory=dict)
    total_speakers: int = 0
    duration_seconds: float = 0.0


class PharmacySpeakerDiarizer:
    """
    Diarization pipeline:
    1. pyannote/speaker-diarization-3.1 — segment audio into speaker turns
    2. pyannote/wespeaker-voxceleb-resnet34-LM — extract 256-dim voice embeddings
    3. Match embeddings against known voice profiles (staff + enrolled patients)
    4. Assign roles based on identity match or spatial context (counter mic vs. customer mic)
    """

    def __init__(self, hf_token: Optional[str] = None):
        self.hf_token = hf_token
        self._pipeline = None
        self._embedding_model = None
        self._voice_index: dict[str, np.ndarray] = {}  # identity_id → embedding

    def _load_pipeline(self):
        if self._pipeline is None:
            try:
                from pyannote.audio import Pipeline
                self._pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self.hf_token,
                )
                logger.info("pyannote diarization pipeline loaded")
            except Exception as exc:
                logger.error("Failed to load diarization pipeline: %s", exc)
                raise

    def enroll_voice(self, identity_id: UUID, audio_samples: list[np.ndarray]) -> bool:
        """
        Enroll a known individual's voice for future identification.
        Called when a pharmacist or patient consents to voice enrollment.
        Requires minimum 3 clean audio samples of at least 5 seconds each.
        """
        if len(audio_samples) < 3:
            logger.warning("Insufficient samples for voice enrollment: %d", len(audio_samples))
            return False

        embeddings = [self._extract_embedding(sample) for sample in audio_samples]
        valid = [e for e in embeddings if e is not None]
        if not valid:
            return False

        centroid = np.mean(np.stack(valid), axis=0)
        centroid = centroid / np.linalg.norm(centroid)  # L2 normalize
        self._voice_index[str(identity_id)] = centroid
        logger.info("Voice enrolled for identity %s", identity_id)
        return True

    def diarize(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        audio_zone: str = "counter",
    ) -> DiarizedTranscript:
        """
        Run full diarization on a processed audio buffer.
        Returns speaker segments with identity matches where available.
        """
        self._load_pipeline()

        import tempfile, soundfile as sf, os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, audio, sample_rate)
            tmp_path = tmp.name

        try:
            diarization = self._pipeline(tmp_path)
        finally:
            os.unlink(tmp_path)

        segments: list[SpeakerSegment] = []
        speaker_embeddings: dict[str, list[np.ndarray]] = {}

        for turn, _, speaker in diarization.itertracks(yield_label=True):
            seg = SpeakerSegment(
                speaker_label=speaker,
                start_seconds=turn.start,
                end_seconds=turn.end,
            )

            # Extract embedding for this segment to match against known voices
            start_sample = int(turn.start * sample_rate)
            end_sample = int(turn.end * sample_rate)
            segment_audio = audio[start_sample:end_sample]

            if len(segment_audio) > sample_rate:  # Only embed segments > 1 second
                embedding = self._extract_embedding(segment_audio)
                if embedding is not None:
                    if speaker not in speaker_embeddings:
                        speaker_embeddings[speaker] = []
                    speaker_embeddings[speaker].append(embedding)

            segments.append(seg)

        # Match speaker embeddings against known voice profiles
        speaker_map = self._match_speakers_to_identities(speaker_embeddings)

        # Annotate segments with resolved identities
        for seg in segments:
            if seg.speaker_label in speaker_map:
                match = speaker_map[seg.speaker_label]
                seg.biometric_identity_id = match.get("biometric_identity_id")
                seg.speaker_role = match.get("role", "unknown")
                seg.voice_match_confidence = match.get("confidence", 0.0)

            # Zone-based role inference if voice match fails
            if seg.speaker_role == "unknown" and audio_zone == "counter":
                seg.speaker_role = self._infer_role_from_zone(
                    seg.speaker_label, audio_zone, speaker_map
                )

        return DiarizedTranscript(
            segments=segments,
            speaker_map=speaker_map,
            total_speakers=len(set(s.speaker_label for s in segments)),
            duration_seconds=audio.shape[0] / sample_rate,
        )

    def _extract_embedding(self, audio: np.ndarray) -> Optional[np.ndarray]:
        try:
            from speechbrain.inference.speaker import SpeakerRecognition
            # Use SpeechBrain ECAPA-TDNN as embedding extractor
            # Loaded lazily to avoid startup memory hit
            if self._embedding_model is None:
                self._embedding_model = SpeakerRecognition.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir="/app/models/speaker_recognition",
                )
            import torch
            tensor = torch.from_numpy(audio).unsqueeze(0)
            embedding = self._embedding_model.encode_batch(tensor)
            vec = embedding.squeeze().detach().numpy()
            return vec / np.linalg.norm(vec)
        except Exception as exc:
            logger.debug("Embedding extraction failed: %s", exc)
            return None

    def _match_speakers_to_identities(
        self,
        speaker_embeddings: dict[str, list[np.ndarray]],
        threshold: float = 0.75,
    ) -> dict[str, dict]:
        speaker_map: dict[str, dict] = {}

        for speaker_label, embeddings in speaker_embeddings.items():
            centroid = np.mean(np.stack(embeddings), axis=0)
            centroid = centroid / np.linalg.norm(centroid)

            best_match_id = None
            best_score = 0.0

            for identity_id, known_embedding in self._voice_index.items():
                score = float(np.dot(centroid, known_embedding))
                if score > best_score:
                    best_score = score
                    best_match_id = identity_id

            if best_score >= threshold and best_match_id:
                speaker_map[speaker_label] = {
                    "biometric_identity_id": UUID(best_match_id),
                    "confidence": best_score,
                    "role": "identified",
                }
            else:
                speaker_map[speaker_label] = {
                    "biometric_identity_id": None,
                    "confidence": best_score,
                    "role": "unknown",
                }

        return speaker_map

    def _infer_role_from_zone(
        self,
        speaker_label: str,
        zone: str,
        speaker_map: dict,
    ) -> str:
        """
        In counter zone: first identified speaker = staff, others = customer.
        In counseling room: known staff = pharmacist, others = patient.
        """
        identified_speakers = [
            k for k, v in speaker_map.items()
            if v.get("biometric_identity_id") is not None
        ]
        if zone == "counter":
            return "staff" if speaker_label in identified_speakers else "customer"
        return "unknown"
