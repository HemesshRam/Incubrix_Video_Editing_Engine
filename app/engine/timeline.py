"""Deterministic, content-anchored editing state.

Word IDs refer to the immutable source transcript. Timeline positions are derived
from the active sequence and speed map, so edits do not retarget attached effects.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Optional


class TimelineEngine:
    def __init__(self, raw_transcript: List[Dict[str, Any]], video_metadata: Dict[str, Any]):
        self.raw_transcript = copy.deepcopy(raw_transcript)
        self.metadata = copy.deepcopy(video_metadata)
        self._word_map = {word["word_id"]: word for word in self.raw_transcript}
        self._source_order = {word_id: index for index, word_id in enumerate(self._word_map)}
        if len(self._word_map) != len(self.raw_transcript):
            raise ValueError("Transcript word IDs must be unique.")
        self.active_word_ids = list(self._word_map)
        self.captions: List[Dict[str, Any]] = []
        self.effects: List[Dict[str, Any]] = []
        self.speed_map: Dict[str, float] = {}
        self.background_music: Optional[Dict[str, Any]] = None
        self.long_pause_threshold_s: Optional[float] = None
        self.history: List[Dict[str, Any]] = []
        self.redo_stack: List[Dict[str, Any]] = []
        self._record_state("initial_state")

    def to_document(self) -> Dict[str, Any]:
        """Serialize source and complete undo/redo state for durable session storage."""
        return {
            "schema_version": 1,
            "raw_transcript": copy.deepcopy(self.raw_transcript),
            "video_metadata": copy.deepcopy(self.metadata),
            "state": self._state(),
            "history": copy.deepcopy(self.history),
            "redo_stack": copy.deepcopy(self.redo_stack),
        }

    @classmethod
    def from_document(cls, document: Dict[str, Any]) -> "TimelineEngine":
        if document.get("schema_version") != 1:
            raise ValueError("Unsupported timeline document version.")
        engine = cls(document["raw_transcript"], document["video_metadata"])
        engine._restore_state(document["state"])
        engine.history = copy.deepcopy(document["history"])
        engine.redo_stack = copy.deepcopy(document["redo_stack"])
        if not engine.history or engine.history[-1].get("hash") != engine.get_current_timeline_hash():
            raise ValueError("Timeline document history does not match its current state.")
        return engine

    def _state(self) -> Dict[str, Any]:
        state = {
            "active_words": list(self.active_word_ids),
            "captions": copy.deepcopy(self.captions),
            "effects": copy.deepcopy(self.effects),
            "speed_map": dict(self.speed_map),
            "background_music": copy.deepcopy(self.background_music),
        }
        if self.long_pause_threshold_s is not None:
            state["long_pause_threshold_s"] = self.long_pause_threshold_s
        return state

    def _compute_hash(self) -> str:
        payload = json.dumps(
            {
                "source_sha256": self.metadata.get("source_sha256"),
                "transcript": self.raw_transcript,
                "state": self._state(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _record_state(self, action_summary: str) -> None:
        snapshot = self._state()
        snapshot.update({"summary": action_summary, "hash": self._compute_hash()})
        self.history.append(snapshot)

    def _validate_ids(self, word_ids: List[str], *, active: bool = True) -> None:
        if not word_ids:
            raise ValueError("At least one word ID is required.")
        unknown = [word_id for word_id in word_ids if word_id not in self._word_map]
        if unknown:
            raise ValueError(f"Unknown word ID(s): {', '.join(unknown)}")
        if active:
            inactive = [word_id for word_id in word_ids if word_id not in self.active_word_ids]
            if inactive:
                raise ValueError("The operation targets words that are no longer active.")

    def _commit(self, summary: str) -> str:
        self.redo_stack.clear()
        self._record_state(summary)
        return summary

    def _prune_removed_anchors(self) -> str:
        """Drop captions/effects without surviving words and retain only live anchors."""
        active = set(self.active_word_ids)
        notices = []
        kept_captions = []
        removed_captions = 0
        trimmed_captions = 0
        for caption in self.captions:
            targets = list(dict.fromkeys(caption.get("target_word_ids", [])))
            surviving = [word_id for word_id in targets if word_id in active]
            if surviving:
                if len(surviving) != len(targets):
                    caption["target_word_ids"] = surviving
                    trimmed_captions += 1
                kept_captions.append(caption)
            elif targets:
                removed_captions += 1
        self.captions = kept_captions
        if removed_captions:
            noun = "caption" if removed_captions == 1 else "captions"
            notices.append(f"Removed {removed_captions} {noun} with no surviving anchored words; undo restores them.")
        if trimmed_captions:
            noun = "caption" if trimmed_captions == 1 else "captions"
            notices.append(f"Updated {trimmed_captions} {noun} to retain only surviving word anchors.")

        kept_effects = []
        removed_effects = 0
        trimmed_effects = 0
        for effect in self.effects:
            targets = list(dict.fromkeys(effect.get("target_word_ids", [])))
            surviving = [word_id for word_id in targets if word_id in active]
            if surviving:
                if len(surviving) != len(targets):
                    effect["target_word_ids"] = surviving
                    trimmed_effects += 1
                kept_effects.append(effect)
            elif targets:
                removed_effects += 1
        self.effects = kept_effects
        if removed_effects:
            noun = "effect" if removed_effects == 1 else "effects"
            notices.append(f"Removed {removed_effects} audio {noun} with no surviving target words; undo restores them.")
        if trimmed_effects:
            noun = "effect" if trimmed_effects == 1 else "effects"
            notices.append(f"Updated {trimmed_effects} audio {noun} to retain only surviving target words.")
        return " ".join(notices)

    def cut_range(self, word_ids_to_cut: List[str]) -> str:
        self._validate_ids(word_ids_to_cut)
        selected = set(word_ids_to_cut)
        remaining = [word_id for word_id in self.active_word_ids if word_id not in selected]
        if not remaining:
            raise ValueError("A cut cannot remove the entire video.")
        self.active_word_ids = remaining
        summary = f"Cut {len(selected)} words."
        caption_notice = self._prune_removed_anchors()
        return self._commit(f"{summary} {caption_notice}".strip())

    def keep_range(self, word_ids_to_keep: List[str]) -> str:
        self._validate_ids(word_ids_to_keep)
        selected = set(word_ids_to_keep)
        kept = [word_id for word_id in self.active_word_ids if word_id in selected]
        if not kept:
            raise ValueError("The keep range does not include any active words.")
        removed = set(self.active_word_ids) - selected
        self.active_word_ids = kept
        summary = f"Kept {len(kept)} words and removed the rest."
        caption_notice = self._prune_removed_anchors() if removed else ""
        return self._commit(f"{summary} {caption_notice}".strip())

    def reorder(self, source_word_ids: List[str], insert_after_word_id: str) -> str:
        self._validate_ids(source_word_ids)
        self._validate_ids([insert_after_word_id])
        source = set(source_word_ids)
        if insert_after_word_id in source:
            raise ValueError("The insertion anchor cannot be part of the moved range.")
        moving = [word_id for word_id in self.active_word_ids if word_id in source]
        remaining = [word_id for word_id in self.active_word_ids if word_id not in source]
        anchor_index = remaining.index(insert_after_word_id)
        self.active_word_ids = remaining[:anchor_index + 1] + moving + remaining[anchor_index + 1:]
        return self._commit(f"Moved {len(moving)} words after {insert_after_word_id}.")

    def change_speed(self, target_word_ids: List[str], speed_factor: float) -> str:
        self._validate_ids(target_word_ids)
        if not 0.25 <= speed_factor <= 4.0:
            raise ValueError("Speed factor must be between 0.25 and 4.0.")
        for word_id in target_word_ids:
            self.speed_map[word_id] = float(speed_factor)
        return self._commit(f"Changed speed for {len(set(target_word_ids))} words to {speed_factor:g}x.")

    def add_effect(self, target_word_ids: List[str], effect_type: str) -> str:
        self._validate_ids(target_word_ids)
        if effect_type not in {"mute", "bleep"}:
            raise ValueError("Audio effect must be 'mute' or 'bleep'.")
        self.effects.append({
            "effect_id": f"effect_{len(self.effects) + 1}",
            "type": effect_type,
            "target_word_ids": list(dict.fromkeys(target_word_ids)),
        })
        return self._commit(f"Applied {effect_type} to {len(set(target_word_ids))} words.")

    def add_caption(self, text: str, target_word_ids: List[str], style: Optional[dict] = None) -> str:
        self._validate_ids(target_word_ids)
        if not text.strip():
            raise ValueError("Caption text cannot be empty.")
        self.captions.append({
            "caption_id": f"cap_{len(self.captions) + 1}",
            "text": text,
            "target_word_ids": list(dict.fromkeys(target_word_ids)),
            "style": copy.deepcopy(style or {"color": "white", "font_size": 24}),
        })
        return self._commit(f"Added caption '{text}' attached to {len(set(target_word_ids))} words.")

    def update_caption(self, caption_id: str, style_attributes: Dict[str, Any]) -> str:
        if not style_attributes:
            raise ValueError("At least one caption style attribute is required.")
        caption = next((item for item in self.captions if item["caption_id"] == caption_id), None)
        if caption is None:
            raise ValueError(f"Caption '{caption_id}' does not exist.")
        allowed = {"color", "font_size", "position", "font_family", "bold", "italic"}
        unknown = sorted(set(style_attributes) - allowed)
        if unknown:
            raise ValueError(f"Unsupported caption style attribute(s): {', '.join(unknown)}")
        style = dict(caption.get("style") or {})
        style.update(copy.deepcopy(style_attributes))
        if "font_size" in style:
            try:
                size = int(style["font_size"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Caption font_size must be an integer from 12 to 96.") from exc
            if not 12 <= size <= 96:
                raise ValueError("Caption font_size must be from 12 to 96.")
            style["font_size"] = size
        caption["style"] = style
        return self._commit(f"Updated style for caption {caption_id}.")

    def auto_caption(self, max_words_per_caption: int = 6) -> str:
        if not 1 <= max_words_per_caption <= 12:
            raise ValueError("max_words_per_caption must be between 1 and 12.")
        if not self.active_word_ids:
            raise ValueError("There are no active words to caption.")
        groups: List[List[str]] = []
        current: List[str] = []
        previous = None
        for word_id in self.active_word_ids:
            word = self._word_map[word_id]
            boundary = False
            if current and previous is not None:
                prior = self._word_map[previous]
                boundary = (
                    len(current) >= max_words_per_caption
                    or word.get("speaker") != prior.get("speaker")
                    or float(word["start"]) - float(prior["end"]) >= 1.0
                    or bool(re.search(r"[.!?][\"')\]]?$", str(prior.get("word", ""))))
                )
            if boundary:
                groups.append(current)
                current = []
            current.append(word_id)
            previous = word_id
        if current:
            groups.append(current)

        start_index = len(self.captions) + 1
        for offset, word_ids in enumerate(groups):
            raw_text = " ".join(str(self._word_map[word_id].get("word", "")).strip() for word_id in word_ids)
            text = re.sub(r"\s+([,.;:!?])", r"\1", raw_text).strip()
            self.captions.append({
                "caption_id": f"cap_{start_index + offset}",
                "text": text,
                "target_word_ids": list(word_ids),
                "style": {"color": "white", "font_size": 24},
            })
        return self._commit(f"Generated {len(groups)} captions from the active transcript.")

    def apply_caption_translations(
        self,
        language: str,
        translations: Dict[str, str],
        *,
        record_history: bool = True,
    ) -> str:
        if not self.captions:
            raise ValueError("There are no captions to translate. Generate captions first.")
        caption_ids = [item["caption_id"] for item in self.captions]
        if set(translations) != set(caption_ids) or len(translations) != len(caption_ids):
            raise ValueError("Translations must provide exactly one result for every current caption.")
        translated_texts = {caption_id: str(text).strip() for caption_id, text in translations.items()}
        if any(not text for text in translated_texts.values()):
            raise ValueError("Caption translations cannot be empty.")
        for caption in self.captions:
            caption["text"] = translated_texts[caption["caption_id"]]
        summary = f"Translated {len(caption_ids)} captions into {language}."
        if record_history:
            return self._commit(summary)
        return summary

    def record_composite_edit(self, summary: str, *, replace_latest: bool = False) -> str:
        if replace_latest:
            if len(self.history) <= 1:
                raise ValueError("There is no edit history to combine.")
            self.history.pop()
        return self._commit(summary)

    def remove_fillers(self, filler_words: Optional[List[str]] = None) -> str:
        defaults = {"um", "uh", "erm", "er", "hmm"}
        normalized = {
            re.sub(r"[^a-z']", "", str(item).lower())
            for item in (filler_words or sorted(defaults))
        }
        normalized &= defaults
        if not normalized:
            raise ValueError("No supported filler words were selected.")
        selected = []
        for word_id in self.active_word_ids:
            token = re.sub(r"[^a-z']", "", str(self._word_map[word_id].get("word", "")).lower())
            if token in normalized:
                selected.append(word_id)
        if not selected:
            raise ValueError("No supported filler words were found in the active transcript.")
        remaining = [word_id for word_id in self.active_word_ids if word_id not in set(selected)]
        if not remaining:
            raise ValueError("Removing filler words cannot remove the entire video.")
        self.active_word_ids = remaining
        summary = f"Removed {len(selected)} filler words."
        caption_notice = self._prune_removed_anchors()
        return self._commit(f"{summary} {caption_notice}".strip())

    def remove_long_pauses(self, threshold_s: float = 0.8) -> str:
        if not 0.2 <= threshold_s <= 5.0:
            raise ValueError("Long-pause threshold must be from 0.2 to 5 seconds.")
        previous = self.long_pause_threshold_s
        if previous == float(threshold_s):
            raise ValueError("Long-pause removal is already enabled at this threshold.")
        self.long_pause_threshold_s = float(threshold_s)
        return self._commit(f"Remove transcript gaps of {threshold_s:g} seconds or longer.")

    def add_bgm(self, audio_url: str, auto_ducking: bool = True) -> str:
        if not audio_url.strip():
            raise ValueError("Background music source cannot be empty.")
        self.background_music = {"audio_url": audio_url, "auto_ducking": bool(auto_ducking)}
        return self._commit("Added background music.")

    def undo(self) -> str:
        if len(self.history) <= 1:
            raise ValueError("No edits to undo.")
        current = self.history.pop()
        self.redo_stack.append(current)
        self._restore_state(self.history[-1])
        return f"Undid: {current['summary']}"

    def redo(self) -> str:
        if not self.redo_stack:
            raise ValueError("No edits to redo.")
        snapshot = self.redo_stack.pop()
        self._restore_state(snapshot)
        self.history.append(copy.deepcopy(snapshot))
        return f"Redid: {snapshot['summary']}"

    def restore_step(self, step_index: int) -> str:
        if step_index < 0 or step_index >= len(self.history):
            raise ValueError(f"History step {step_index} does not exist.")
        snapshot = copy.deepcopy(self.history[step_index])
        self._restore_state(snapshot)
        return self._commit(f"Restored timeline to step {step_index}.")

    def _restore_state(self, snapshot: Dict[str, Any]) -> None:
        self.active_word_ids = list(snapshot["active_words"])
        self.captions = copy.deepcopy(snapshot["captions"])
        self.effects = copy.deepcopy(snapshot["effects"])
        self.speed_map = dict(snapshot["speed_map"])
        self.background_music = copy.deepcopy(snapshot.get("background_music"))
        self.long_pause_threshold_s = snapshot.get("long_pause_threshold_s")

    def apply_operations(self, operations: List[Any]) -> List[str]:
        """Apply one user instruction as one history step; roll back on any error."""
        normalized = [op.model_dump() if hasattr(op, "model_dump") else dict(op) for op in operations]
        history_controls = [op for op in normalized if op.get("op") == "history_control"]
        if history_controls and (len(normalized) != 1 or len(history_controls) != 1):
            raise ValueError("Undo, redo, and restore must be sent as a single operation.")
        before = self._state()
        history_before = copy.deepcopy(self.history)
        redo_before = copy.deepcopy(self.redo_stack)
        summaries: List[str] = []
        try:
            for value in normalized:
                value = dict(value)
                kind = value.pop("op")
                if kind == "cut_range":
                    summaries.append(self.cut_range(value["target_word_ids"]))
                elif kind == "keep_range":
                    summaries.append(self.keep_range(value["target_word_ids"]))
                elif kind == "reorder":
                    summaries.append(self.reorder(value["source_word_ids"], value["insert_after_word_id"]))
                elif kind == "change_speed":
                    summaries.append(self.change_speed(value["target_word_ids"], value["speed_factor"]))
                elif kind == "audio_effect":
                    summaries.append(self.add_effect(value["target_word_ids"], value["effect_type"]))
                elif kind == "add_caption":
                    summaries.append(self.add_caption(value["text"], value["target_word_ids"], value.get("style_attributes")))
                elif kind == "update_caption":
                    summaries.append(self.update_caption(value["caption_id"], value["style_attributes"]))
                elif kind == "auto_caption":
                    summaries.append(self.auto_caption(value.get("max_words_per_caption", 6)))
                elif kind == "remove_fillers":
                    summaries.append(self.remove_fillers(value.get("filler_words")))
                elif kind == "remove_long_pauses":
                    summaries.append(self.remove_long_pauses(value.get("threshold_s", 0.8)))
                elif kind == "add_bgm":
                    summaries.append(self.add_bgm(value["audio_url"], value.get("auto_ducking", True)))
                elif kind == "history_control":
                    if value["action"] == "undo":
                        summaries.append(self.undo())
                    elif value["action"] == "redo":
                        summaries.append(self.redo())
                    else:
                        if value.get("target_step_index") is None:
                            raise ValueError("target_step_index is required for restore_step.")
                        summaries.append(self.restore_step(value["target_step_index"]))
                else:
                    raise ValueError(f"Unsupported operation: {kind}")
        except Exception:
            self._restore_state(before)
            self.history = history_before
            self.redo_stack = redo_before
            raise
        if normalized and not history_controls:
            self.history = history_before
            self._record_state("; ".join(summaries))
        return summaries

    def get_current_timeline_hash(self) -> str:
        return self._compute_hash()

    def get_duration(self) -> float:
        """Return the duration of the source spans the renderer will concatenate."""
        from app.engine.renderer import VideoRenderer

        runs = VideoRenderer._segment_runs(
            self.active_word_ids,
            self._word_map,
            self.speed_map,
            self.long_pause_threshold_s,
            self.metadata.get("duration"),
        )
        return round(sum((run["end"] - run["start"]) / run["speed"] for run in runs), 6)

    def get_active_transcript_context(self, max_words: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return current-order word anchors and derived times for model interpretation."""
        if max_words is not None and len(self.active_word_ids) > max_words:
            raise ValueError(f"Transcript has {len(self.active_word_ids)} active words; limit is {max_words}.")
        from app.engine.renderer import VideoRenderer

        timing = VideoRenderer._current_word_times(
            self.active_word_ids,
            self._word_map,
            self.speed_map,
            self.long_pause_threshold_s,
        )
        context = []
        for word_id in self.active_word_ids:
            word = self._word_map[word_id]
            context.append({
                "id": word_id,
                "word": word.get("word", ""),
                "speaker": word.get("speaker"),
                "current_start": round(timing[word_id]["start"], 3),
                "current_end": round(timing[word_id]["end"], 3),
            })
        return context
