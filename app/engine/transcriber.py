import hashlib
import ffmpeg
from typing import List, Dict, Any, Optional
from faster_whisper import WhisperModel
from app.core.config import get_settings

class Transcriber:
    def __init__(self, model_size: Optional[str] = None, device: Optional[str] = None, compute_type: Optional[str] = None, language: Optional[str] = None):
        settings = get_settings()
        self.model_size = model_size or settings.whisper_model_size
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type
        self.language = language or settings.whisper_language
        self.detected_language: Optional[str] = self.language
        self.model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)

    def extract_metadata(self, video_path: str) -> Dict[str, Any]:
        """Extracts video duration, frame rate, audio presence, and resolution."""
        probe = ffmpeg.probe(video_path)
        video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)

        if not video_stream:
            raise ValueError("No video stream found in media file.")

        fps_eval = video_stream.get('r_frame_rate', '30/1').split('/')
        fps = float(fps_eval[0]) / float(fps_eval[1]) if len(fps_eval) == 2 else float(fps_eval[0])
        duration = float(probe['format'].get('duration', 0.0))

        digest = hashlib.sha256()
        with open(video_path, "rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)

        return {
            "source_sha256": digest.hexdigest(),
            "duration": duration,
            "fps": fps,
            "width": int(video_stream.get('width', 0)),
            "height": int(video_stream.get('height', 0)),
            "has_audio": audio_stream is not None
        }

    def transcribe(self, video_path: str) -> List[Dict[str, Any]]:
        """Extracts word-level timing with unique word_ids and confidence scores."""
        segments, info = self.model.transcribe(video_path, word_timestamps=True, language=self.language)
        self.detected_language = getattr(info, "language", None) or self.language
        words_data = []

        for segment in segments:
            speaker = getattr(segment, "speaker", "speaker_0")
            for word in segment.words:
                start = round(float(word.start), 3)
                end = round(float(word.end), 3)
                # Whisper can emit sub-millisecond tokens. Independent rounding
                # can collapse their interval, but the API requires end > start.
                if end <= start:
                    end = round(start + 0.001, 3)
                words_data.append({
                    # Stable identity within the immutable source transcript.
                    "word_id": f"w_{len(words_data) + 1:08d}",
                    "word": word.word.strip(),
                    "start": start,
                    "end": end,
                    "speaker": speaker,
                    "confidence": round(word.probability, 3)
                })

        return words_data
