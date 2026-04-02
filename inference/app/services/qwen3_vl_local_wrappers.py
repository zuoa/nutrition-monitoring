import logging
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from qwen_vl_utils.vision_process import process_vision_info
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
from transformers.cache_utils import Cache
from transformers.modeling_outputs import ModelOutput
from transformers.models.qwen3_vl.modeling_qwen3_vl import (
    Qwen3VLConfig,
    Qwen3VLModel,
    Qwen3VLPreTrainedModel,
)
from transformers.models.qwen3_vl.processing_qwen3_vl import Qwen3VLProcessor
from transformers.processing_utils import Unpack
from transformers.utils import TransformersKwargs

logger = logging.getLogger(__name__)

MAX_LENGTH = 8192
IMAGE_BASE_FACTOR = 16
IMAGE_FACTOR = IMAGE_BASE_FACTOR * 2
MIN_PIXELS = 4 * IMAGE_FACTOR * IMAGE_FACTOR
MAX_PIXELS = 1800 * IMAGE_FACTOR * IMAGE_FACTOR
FPS = 1
MAX_FRAMES = 64
FRAME_MAX_PIXELS = 768 * IMAGE_FACTOR * IMAGE_FACTOR
MAX_TOTAL_PIXELS = 10 * FRAME_MAX_PIXELS

RERANKER_MAX_PIXELS = 1280 * IMAGE_FACTOR * IMAGE_FACTOR
RERANKER_FRAME_FACTOR = 2
RERANKER_MAX_TOTAL_PIXELS = 4 * RERANKER_FRAME_FACTOR * RERANKER_MAX_PIXELS


def coerce_token_ids(token_ids: Any) -> List[int]:
    if isinstance(token_ids, torch.Tensor):
        token_ids = token_ids.detach().cpu().tolist()
    elif isinstance(token_ids, np.ndarray):
        token_ids = token_ids.tolist()
    return [int(token_id) for token_id in token_ids]


def sample_frames(frames: List[str], num_segments: int, max_segments: int) -> List[str]:
    duration = len(frames)
    frame_id_array = np.linspace(0, duration - 1, num_segments, dtype=int)
    frame_id_list = frame_id_array.tolist()
    last_frame_id = frame_id_list[-1]

    sampled_frames = []
    for frame_idx in frame_id_list:
        try:
            sampled_frames.append(frames[frame_idx])
        except Exception:
            break

    while len(sampled_frames) < num_segments:
        sampled_frames.append(frames[last_frame_id])
    return sampled_frames[:max_segments]


@dataclass
class Qwen3VLForEmbeddingOutput(ModelOutput):
    last_hidden_state: Optional[torch.FloatTensor] = None
    attention_mask: Optional[torch.Tensor] = None


class Qwen3VLForEmbedding(Qwen3VLPreTrainedModel):
    _checkpoint_conversion_mapping = {}
    accepts_loss_kwargs = False
    config: Qwen3VLConfig

    def __init__(self, config):
        super().__init__(config)
        self.model = Qwen3VLModel(config)
        self.post_init()

    def get_input_embeddings(self):
        return self.model.get_input_embeddings()

    def set_input_embeddings(self, value):
        self.model.set_input_embeddings(value)

    def set_decoder(self, decoder):
        self.model.set_decoder(decoder)

    def get_decoder(self):
        return self.model.get_decoder()

    def get_video_features(self, pixel_values_videos: torch.FloatTensor, video_grid_thw: Optional[torch.LongTensor] = None):
        return self.model.get_video_features(pixel_values_videos, video_grid_thw)

    def get_image_features(self, pixel_values: torch.FloatTensor, image_grid_thw: Optional[torch.LongTensor] = None):
        return self.model.get_image_features(pixel_values, image_grid_thw)

    @property
    def language_model(self):
        return self.model.language_model

    @property
    def visual(self):
        return self.model.visual

    def forward(
        self,
        input_ids: torch.LongTensor = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_values: Optional[Cache] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        pixel_values: Optional[torch.Tensor] = None,
        pixel_values_videos: Optional[torch.FloatTensor] = None,
        image_grid_thw: Optional[torch.LongTensor] = None,
        video_grid_thw: Optional[torch.LongTensor] = None,
        cache_position: Optional[torch.LongTensor] = None,
        logits_to_keep: Union[int, torch.Tensor] = 0,
        **kwargs: Unpack[TransformersKwargs],
    ) -> Union[tuple, Qwen3VLForEmbeddingOutput]:
        outputs = self.model(
            input_ids=input_ids,
            pixel_values=pixel_values,
            pixel_values_videos=pixel_values_videos,
            image_grid_thw=image_grid_thw,
            video_grid_thw=video_grid_thw,
            position_ids=position_ids,
            attention_mask=attention_mask,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            cache_position=cache_position,
            **kwargs,
        )
        return Qwen3VLForEmbeddingOutput(
            last_hidden_state=outputs.last_hidden_state,
            attention_mask=attention_mask,
        )


class Qwen3VLEmbedder:
    def __init__(
        self,
        model_name_or_path: str,
        max_length: int = MAX_LENGTH,
        instruction: Optional[str] = None,
        **kwargs,
    ):
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.max_length = max_length
        self.instruction = instruction or "Represent the user's input."
        self.min_pixels = kwargs.pop("min_pixels", MIN_PIXELS)
        self.max_pixels = kwargs.pop("max_pixels", MAX_PIXELS)
        self.total_pixels = kwargs.pop("total_pixels", MAX_TOTAL_PIXELS)
        self.fps = kwargs.pop("fps", FPS)
        self.num_frames = kwargs.pop("num_frames", MAX_FRAMES)
        self.max_frames = kwargs.pop("max_frames", MAX_FRAMES)

        self.model = Qwen3VLForEmbedding.from_pretrained(
            model_name_or_path,
            trust_remote_code=True,
            **kwargs,
        ).to(device)
        self.processor = Qwen3VLProcessor.from_pretrained(
            model_name_or_path,
            padding_side="right",
        )
        self.model.eval()

    @torch.no_grad()
    def forward(self, inputs: Dict[str, Any]) -> Dict[str, torch.Tensor]:
        outputs = self.model(**inputs)
        return {
            "last_hidden_state": outputs.last_hidden_state,
            "attention_mask": inputs.get("attention_mask"),
        }

    def _truncate_tokens(self, token_ids: List[int], max_length: int) -> List[int]:
        if len(token_ids) <= max_length:
            return token_ids

        special_token_ids = set(self.processor.tokenizer.all_special_ids)
        num_special = sum(1 for token_idx in token_ids if token_idx in special_token_ids)
        num_non_special_to_keep = max_length - num_special

        final_token_ids = []
        non_special_kept_count = 0
        for token_idx in token_ids:
            if token_idx in special_token_ids:
                final_token_ids.append(token_idx)
            elif non_special_kept_count < num_non_special_to_keep:
                final_token_ids.append(token_idx)
                non_special_kept_count += 1
        return final_token_ids

    def format_model_input(
        self,
        text: Optional[str] = None,
        image: Optional[Union[str, Image.Image]] = None,
        video: Optional[Union[str, List[str]]] = None,
        instruction: Optional[str] = None,
        fps: Optional[float] = None,
        max_frames: Optional[int] = None,
    ) -> List[Dict]:
        if instruction:
            instruction = instruction.strip()
            if instruction and not unicodedata.category(instruction[-1]).startswith("P"):
                instruction = instruction + "."

        content = []
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": instruction or self.instruction}]},
            {"role": "user", "content": content},
        ]

        if not text and not image and not video:
            content.append({"type": "text", "text": ""})
            return conversation

        if video:
            video_content = None
            if isinstance(video, list):
                video_content = video
                if self.num_frames is not None or self.max_frames is not None:
                    video_content = sample_frames(video_content, self.num_frames, self.max_frames)
                video_content = ["file://" + ele for ele in video_content]
            elif isinstance(video, str):
                video_content = video if video.startswith(("http", "oss")) else "file://" + video
            else:
                video_content = video

            if video_content:
                content.append({
                    "type": "video",
                    "video": video_content,
                    "total_pixels": self.total_pixels,
                    "max_frames": max_frames or self.max_frames,
                    "fps": fps or self.fps,
                    "sample_fps": fps or self.fps,
                })

        if image:
            if isinstance(image, Image.Image):
                image_content = image
            elif isinstance(image, str):
                image_content = image if image.startswith(("http", "oss")) else "file://" + image
            else:
                image_content = image

            content.append({
                "type": "image",
                "image": image_content,
                "min_pixels": self.min_pixels,
                "max_pixels": self.max_pixels,
            })

        if text:
            content.append({"type": "text", "text": text})
        return conversation

    def _preprocess_inputs(self, conversations: List[List[Dict]]) -> Dict[str, torch.Tensor]:
        text = self.processor.apply_chat_template(
            conversations,
            add_generation_prompt=True,
            tokenize=False,
        )

        try:
            images, video_inputs, video_kwargs = process_vision_info(
                conversations,
                image_patch_size=16,
                return_video_metadata=True,
                return_video_kwargs=True,
            )
        except Exception as e:
            logger.warning("Error in processing vision info: %s", e)
            images = None
            video_inputs = None
            video_kwargs = {"do_sample_frames": False}
            text = self.processor.apply_chat_template(
                [{"role": "user", "content": [{"type": "text", "text": "NULL"}]}],
                add_generation_prompt=True,
                tokenize=False,
            )

        if video_inputs is not None:
            videos, video_metadata = zip(*video_inputs)
            videos = list(videos)
            video_metadata = list(video_metadata)
        else:
            videos = None
            video_metadata = None

        return self.processor(
            text=text,
            images=images,
            videos=videos,
            video_metadata=video_metadata,
            truncation=True,
            max_length=self.max_length,
            padding=True,
            do_resize=False,
            return_tensors="pt",
            **video_kwargs,
        )

    @staticmethod
    def _pooling_last(hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        flipped_tensor = attention_mask.flip(dims=[1])
        last_one_positions = flipped_tensor.argmax(dim=1)
        col = attention_mask.shape[1] - last_one_positions - 1
        row = torch.arange(hidden_state.shape[0], device=hidden_state.device)
        return hidden_state[row, col]

    def process(self, inputs: List[Dict[str, Any]], normalize: bool = True) -> torch.Tensor:
        conversations = [
            self.format_model_input(
                text=ele.get("text"),
                image=ele.get("image"),
                video=ele.get("video"),
                instruction=ele.get("instruction"),
                fps=ele.get("fps"),
                max_frames=ele.get("max_frames"),
            )
            for ele in inputs
        ]
        processed_inputs = self._preprocess_inputs(conversations)
        processed_inputs = {k: v.to(self.model.device) for k, v in processed_inputs.items()}
        outputs = self.forward(processed_inputs)
        embeddings = self._pooling_last(outputs["last_hidden_state"], outputs["attention_mask"])
        if normalize:
            embeddings = F.normalize(embeddings, p=2, dim=-1)
        return embeddings


class Qwen3VLReranker:
    def __init__(self, model_name_or_path: str, **kwargs):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.max_length = kwargs.pop("max_length", MAX_LENGTH)
        self.default_instruction = "Given a search query, retrieve relevant candidates that answer the query."
        self.min_pixels = kwargs.pop("min_pixels", MIN_PIXELS)
        self.max_pixels = kwargs.pop("max_pixels", RERANKER_MAX_PIXELS)
        self.total_pixels = kwargs.pop("total_pixels", RERANKER_MAX_TOTAL_PIXELS)
        self.fps = kwargs.pop("fps", FPS)
        self.num_frames = kwargs.pop("num_frames", None)
        self.max_frames = kwargs.pop("max_frames", None)

        lm = Qwen3VLForConditionalGeneration.from_pretrained(
            model_name_or_path,
            trust_remote_code=True,
            **kwargs,
        ).to(self.device)
        self.model = lm.model
        self.processor = AutoProcessor.from_pretrained(
            model_name_or_path,
            trust_remote_code=True,
            padding_side="left",
        )
        token_true_id = self.processor.tokenizer.get_vocab()["yes"]
        token_false_id = self.processor.tokenizer.get_vocab()["no"]
        self.score_linear = self.get_binary_linear(lm, token_true_id, token_false_id)
        self.model.eval()
        self.score_linear.eval()
        self.score_linear.to(self.device).to(self.model.dtype)

    def get_binary_linear(self, model, token_yes, token_no):
        lm_head_weights = model.lm_head.weight.data
        weight_yes = lm_head_weights[token_yes]
        weight_no = lm_head_weights[token_no]
        dimension = weight_yes.size()[0]
        linear_layer = torch.nn.Linear(dimension, 1, bias=False)
        with torch.no_grad():
            linear_layer.weight[0] = weight_yes - weight_no
        return linear_layer

    @torch.no_grad()
    def compute_scores(self, inputs):
        normalized_inputs = self._normalize_model_inputs(inputs)
        batch_scores = self.model(**normalized_inputs).last_hidden_state[:, -1]
        scores = self.score_linear(batch_scores)
        return torch.sigmoid(scores).squeeze(-1).cpu().detach().tolist()

    def _summarize_pairs(self, pairs: list) -> list[dict[str, Any]]:
        summary = []
        for pair in pairs:
            item = {"roles": [], "content_counts": []}
            if isinstance(pair, list):
                for message in pair:
                    item["roles"].append(message.get("role"))
                    contents = message.get("content") or []
                    content_summary = {"text": 0, "image": 0, "video": 0}
                    for content in contents:
                        content_type = str(content.get("type") or "")
                        if content_type in content_summary:
                            content_summary[content_type] += 1
                    item["content_counts"].append(content_summary)
            summary.append(item)
        return summary

    def _summarize_batch_inputs(self, inputs: Any) -> dict[str, Any]:
        if not isinstance(inputs, Mapping):
            return {"type": type(inputs).__name__}
        summary = {}
        for key, value in inputs.items():
            shape = getattr(value, "shape", None)
            if shape is not None:
                try:
                    summary[key] = {"type": type(value).__name__, "shape": list(shape)}
                    continue
                except TypeError:
                    pass
            if isinstance(value, list):
                summary[key] = {"type": "list", "len": len(value)}
            else:
                summary[key] = {"type": type(value).__name__}
        return summary

    def _normalize_model_inputs(self, inputs: Any) -> dict[str, Any]:
        if not isinstance(inputs, Mapping):
            raise TypeError(f"Unsupported reranker inputs type: {type(inputs)}")

        normalized: dict[str, Any] = {}
        tensor_like_keys = {
            "input_ids",
            "attention_mask",
            "mm_token_type_ids",
            "token_type_ids",
            "position_ids",
            "pixel_values",
            "pixel_values_videos",
            "image_grid_thw",
            "video_grid_thw",
        }

        for key, value in inputs.items():
            if isinstance(value, torch.Tensor):
                normalized[key] = value.to(self.model.device)
                continue
            if isinstance(value, np.ndarray):
                normalized[key] = torch.as_tensor(value, device=self.model.device)
                continue
            if key in tensor_like_keys:
                normalized[key] = torch.as_tensor(value, device=self.model.device)
                continue
            normalized[key] = value
        return normalized

    def truncate_tokens_optimized(self, tokens: List[int], max_length: int, special_tokens: List[int]) -> List[int]:
        tokens = coerce_token_ids(tokens)
        if len(tokens) <= max_length:
            return tokens

        special_tokens_set = set(special_tokens)
        num_special = sum(1 for token in tokens if token in special_tokens_set)
        num_non_special_to_keep = max_length - num_special

        final_tokens = []
        non_special_kept_count = 0
        for token in tokens:
            if token in special_tokens_set:
                final_tokens.append(token)
            elif non_special_kept_count < num_non_special_to_keep:
                final_tokens.append(token)
                non_special_kept_count += 1
        return final_tokens

    def tokenize(self, pairs: list, **kwargs):
        logger.debug("Reranker tokenize start: pairs=%s", self._summarize_pairs(pairs))
        text = self.processor.apply_chat_template(pairs, tokenize=False, add_generation_prompt=True)
        try:
            images, videos, video_kwargs = process_vision_info(
                pairs,
                image_patch_size=16,
                return_video_kwargs=True,
                return_video_metadata=True,
            )
        except Exception:
            logger.exception("Error in processing vision info: pairs=%s", self._summarize_pairs(pairs))
            images = None
            videos = None
            video_kwargs = {"do_sample_frames": False}
            fallback_pairs = [[{"role": "user", "content": [{"type": "text", "text": "NULL"}]}]]
            text = self.processor.apply_chat_template(
                fallback_pairs,
                add_generation_prompt=True,
                tokenize=False,
            )

        if videos is not None:
            videos, video_metadatas = zip(*videos)
            videos = list(videos)
            video_metadatas = list(video_metadatas)
        else:
            video_metadatas = None

        try:
            inputs = self.processor(
                text=text,
                images=images,
                videos=videos,
                video_metadata=video_metadatas,
                truncation=False,
                padding=False,
                do_resize=False,
                **video_kwargs,
            )
        except Exception:
            logger.exception(
                "Reranker processor failed: pairs=%s image_count=%s video_count=%s video_kwargs=%s",
                self._summarize_pairs(pairs),
                len(images) if images is not None else 0,
                len(videos) if videos is not None else 0,
                video_kwargs,
            )
            raise
        input_rows = [coerce_token_ids(row) for row in inputs["input_ids"]]
        for i, _ in enumerate(input_rows):
            input_rows[i] = (
                self.truncate_tokens_optimized(
                    input_rows[i][:-5],
                    self.max_length,
                    self.processor.tokenizer.all_special_ids,
                ) + input_rows[i][-5:]
            )
        inputs["input_ids"] = input_rows
        temp_inputs = self.processor.tokenizer.pad(
            {"input_ids": inputs["input_ids"]},
            padding=True,
            return_tensors="pt",
            max_length=self.max_length,
        )
        for key in temp_inputs:
            inputs[key] = temp_inputs[key]
        logger.debug("Reranker tokenize done: batch=%s", self._summarize_batch_inputs(inputs))
        return inputs

    def format_mm_content(self, text, image, video, prefix="Query:", fps=None, max_frames=None):
        content = []
        content.append({"type": "text", "text": prefix})
        if not text and not image and not video:
            content.append({"type": "text", "text": "NULL"})
            return content

        if video:
            video_content = None
            video_kwargs = {"total_pixels": self.total_pixels}
            if isinstance(video, list):
                video_content = video
                if self.num_frames is not None or self.max_frames is not None:
                    video_content = sample_frames(video_content, self.num_frames, self.max_frames)
                video_content = [
                    ("file://" + ele if isinstance(ele, str) else ele)
                    for ele in video_content
                ]
            elif isinstance(video, str):
                video_content = video if video.startswith(("http://", "https://")) else "file://" + video
                video_kwargs = {
                    "fps": fps or self.fps,
                    "max_frames": max_frames or self.max_frames,
                }
            else:
                raise TypeError(f"Unrecognized video type: {type(video)}")
            if video_content:
                content.append({
                    "type": "video",
                    "video": video_content,
                    **video_kwargs,
                })

        if image:
            if isinstance(image, Image.Image):
                image_content = image
            elif isinstance(image, str):
                image_content = image if image.startswith(("http", "oss")) else "file://" + image
            else:
                raise TypeError(f"Unrecognized image type: {type(image)}")
            if image_content:
                content.append({
                    "type": "image",
                    "image": image_content,
                    "min_pixels": self.min_pixels,
                    "max_pixels": self.max_pixels,
                })

        if text:
            content.append({"type": "text", "text": text})
        return content

    def format_mm_instruction(
        self,
        query_text,
        query_image,
        query_video,
        doc_text,
        doc_image,
        doc_video,
        instruction=None,
        fps=None,
        max_frames=None,
    ):
        inputs = [{
            "role": "system",
            "content": [{
                "type": "text",
                "text": 'Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".',
            }],
        }]
        if isinstance(query_text, tuple):
            instruct, query_text = query_text
        else:
            instruct = instruction
        contents = [{"type": "text", "text": "<Instruct>: " + instruct}]
        contents.extend(
            self.format_mm_content(
                query_text,
                query_image,
                query_video,
                prefix="<Query>:",
                fps=fps,
                max_frames=max_frames,
            )
        )
        contents.extend(
            self.format_mm_content(
                doc_text,
                doc_image,
                doc_video,
                prefix="\n<Document>:",
                fps=fps,
                max_frames=max_frames,
            )
        )
        inputs.append({"role": "user", "content": contents})
        return inputs

    def process(self, inputs) -> list[float]:
        instruction = inputs.get("instruction", self.default_instruction)
        query = inputs.get("query", {})
        documents = inputs.get("documents", [])
        if not query or not documents:
            return []

        logger.debug(
            "Reranker process start: query_text=%s query_image=%s query_video=%s document_count=%s",
            bool(query.get("text")),
            bool(query.get("image")),
            bool(query.get("video")),
            len(documents),
        )

        pairs = [
            self.format_mm_instruction(
                query.get("text"),
                query.get("image"),
                query.get("video"),
                document.get("text"),
                document.get("image"),
                document.get("video"),
                instruction=instruction,
                fps=inputs.get("fps", self.fps),
                max_frames=inputs.get("max_frames", self.max_frames),
            )
            for document in documents
        ]
        final_scores = []
        for index, pair in enumerate(pairs, start=1):
            model_inputs = self.tokenize([pair])
            model_inputs = model_inputs.to(self.model.device)
            try:
                scores = self.compute_scores(model_inputs)
            except Exception:
                logger.exception(
                    "Reranker compute_scores failed: pair_index=%s pair=%s batch=%s",
                    index,
                    self._summarize_pairs([pair]),
                    self._summarize_batch_inputs(model_inputs),
                )
                raise
            final_scores.extend(scores)
        return final_scores
