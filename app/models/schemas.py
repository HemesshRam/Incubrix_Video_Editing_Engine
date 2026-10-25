from typing import Literal, Union, List, Optional
from pydantic import BaseModel, Field, model_validator

# --- ATOMIC EDIT OPERATIONS ---

class CutRangeOp(BaseModel):
    op: Literal["cut_range"] = "cut_range"
    target_word_ids: List[str] = Field(min_length=1, description="List of word IDs to cut")
    snap_to_silence: bool = Field(default=True, description="Snap cut boundaries to nearest silence")

class KeepRangeOp(BaseModel):
    op: Literal["keep_range"] = "keep_range"
    target_word_ids: List[str] = Field(min_length=1, description="List of word IDs to retain exclusively")

class ReorderOp(BaseModel):
    op: Literal["reorder"] = "reorder"
    source_word_ids: List[str] = Field(min_length=1, description="Word IDs being moved")
    insert_after_word_id: str = Field(description="Anchor word ID after which source is placed")

class ChangeSpeedOp(BaseModel):
    op: Literal["change_speed"] = "change_speed"
    target_word_ids: List[str] = Field(min_length=1, description="Word IDs affected")
    speed_factor: float = Field(ge=0.25, le=4.0, description="Speed factor from 0.25x through 4x")

class AudioEffectOp(BaseModel):
    op: Literal["audio_effect"] = "audio_effect"
    target_word_ids: List[str] = Field(min_length=1)
    effect_type: Literal["mute", "bleep"]

class AddCaptionOp(BaseModel):
    op: Literal["add_caption"] = "add_caption"
    text: str
    target_word_ids: List[str] = Field(min_length=1, description="Words over which caption displays")
    style_attributes: Optional[dict] = Field(default=None)

class UpdateCaptionOp(BaseModel):
    op: Literal["update_caption"] = "update_caption"
    caption_id: str = Field(min_length=1)
    style_attributes: dict = Field(min_length=1)

class AutoCaptionOp(BaseModel):
    op: Literal["auto_caption"] = "auto_caption"
    max_words_per_caption: int = Field(default=6, ge=1, le=12)

class TranslateCaptionsOp(BaseModel):
    op: Literal["translate_captions"] = "translate_captions"
    language: str = Field(min_length=2, max_length=64, description="Target language name or BCP 47 language tag")

class RemoveFillersOp(BaseModel):
    op: Literal["remove_fillers"] = "remove_fillers"
    filler_words: List[str] = Field(default_factory=lambda: ["um", "uh", "erm", "er", "hmm"])

class RemoveLongPausesOp(BaseModel):
    op: Literal["remove_long_pauses"] = "remove_long_pauses"
    threshold_s: float = Field(default=0.8, ge=0.2, le=5.0)

class AddBGMOp(BaseModel):
    op: Literal["add_bgm"] = "add_bgm"
    audio_url: str
    auto_ducking: bool = True

class HistoryControlOp(BaseModel):
    op: Literal["history_control"] = "history_control"
    action: Literal["undo", "redo", "restore_step"]
    target_step_index: Optional[int] = None

    @model_validator(mode="after")
    def require_restore_index(self):
        if self.action == "restore_step" and self.target_step_index is None:
            raise ValueError("target_step_index is required for restore_step")
        if self.action != "restore_step" and self.target_step_index is not None:
            raise ValueError("target_step_index is only valid for restore_step")
        return self

# --- THREE-WAY RESPONSE SCHEMAS ---

class AppliedResponse(BaseModel):
    status: Literal["applied"] = "applied"
    operations: List[Union[CutRangeOp, KeepRangeOp, ReorderOp, ChangeSpeedOp, AudioEffectOp, AddCaptionOp, UpdateCaptionOp, AutoCaptionOp, TranslateCaptionsOp, RemoveFillersOp, RemoveLongPausesOp, AddBGMOp, HistoryControlOp]] = Field(min_length=1)
    summary: str = Field(description="Human-readable summary of changes made")
    time_policy_used: Literal["original_time", "current_time"] = "current_time"

class ClarificationOption(BaseModel):
    option_id: str
    description: str
    start_time_current: str
    word_snippet: str

class NeedsClarificationResponse(BaseModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    question: str = Field(description="Clear, non-technical question asking for details")
    options: List[ClarificationOption] = Field(min_length=1)

class RejectedResponse(BaseModel):
    status: Literal["rejected"] = "rejected"
    reason: str = Field(description="Explanation of why the prompt cannot be executed")
    conflicting_step_index: Optional[int] = None

class PromptInterpretationResult(BaseModel):
    result: Union[AppliedResponse, NeedsClarificationResponse, RejectedResponse]


class SessionEditResponse(BaseModel):
    result: Union[AppliedResponse, NeedsClarificationResponse, RejectedResponse]
    revision: int
    timeline_hash: str
    duration_s: float


class TranscriptWord(BaseModel):
    word_id: str = Field(min_length=1)
    word: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def end_must_follow_start(self):
        if self.end <= self.start:
            raise ValueError("Transcript word end must be after its start.")
        return self


class VideoMetadata(BaseModel):
    source_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    duration: float = Field(gt=0)
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    has_audio: bool
    source_uri: Optional[str] = None
    transcript_language: Optional[str] = None


class CreateSessionRequest(BaseModel):
    transcript: List[TranscriptWord] = Field(min_length=1)
    video_metadata: VideoMetadata


class ApplyOperationsRequest(BaseModel):
    operations: List[Union[CutRangeOp, KeepRangeOp, ReorderOp, ChangeSpeedOp, AudioEffectOp, AddCaptionOp, UpdateCaptionOp, AutoCaptionOp, TranslateCaptionsOp, RemoveFillersOp, RemoveLongPausesOp, AddBGMOp, HistoryControlOp]] = Field(min_length=1)


class SessionPromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class TimelineSessionResponse(BaseModel):
    session_id: str
    revision: int
    history_depth: int = Field(ge=0)
    redo_depth: int = Field(ge=0)
    timeline_hash: str
    duration_s: float
    active_word_ids: List[str]
    captions: List[dict]
    effects: List[dict]
    speed_map: dict
    background_music: Optional[dict] = None
    long_pause_threshold_s: Optional[float] = None
    operation_summaries: List[str] = Field(default_factory=list)
    transcript: List[TranscriptWord] = Field(default_factory=list)
    video_metadata: Optional[VideoMetadata] = None


class CaptionTranslation(BaseModel):
    caption_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class CaptionTranslationResult(BaseModel):
    captions: List[CaptionTranslation] = Field(min_length=1)


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    status_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int = Field(ge=0, le=100)
    created_at: float
    updated_at: float
    result: Optional[dict] = None
    error: Optional[dict] = None
