import importlib.util
import os
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "qwen3_vl_local_wrappers.py",
)


def load_wrappers_module():
    torch_module = types.ModuleType("torch")
    torch_module.Tensor = type("Tensor", (), {})
    torch_module.FloatTensor = type("FloatTensor", (), {})
    torch_module.LongTensor = type("LongTensor", (), {})
    torch_module.device = lambda value: value
    torch_module.bfloat16 = "bfloat16"
    torch_module.float16 = "float16"
    torch_module.float32 = "float32"
    torch_module.no_grad = lambda: (lambda fn: fn)
    torch_module.sigmoid = lambda value: value
    torch_module.as_tensor = lambda value, device=None: FakeTensor(value, device=device)
    torch_module.cuda = types.SimpleNamespace(
        is_available=lambda: False,
        is_bf16_supported=lambda: False,
    )
    torch_nn_module = types.ModuleType("torch.nn")
    torch_nn_module.Linear = type("Linear", (), {"__init__": lambda self, *args, **kwargs: None})
    torch_module.nn = torch_nn_module

    functional_module = types.ModuleType("torch.nn.functional")
    functional_module.normalize = lambda tensor, p=2, dim=-1: tensor

    pil_module = types.ModuleType("PIL")
    image_module = types.ModuleType("PIL.Image")
    image_module.Image = type("Image", (), {})
    pil_module.Image = image_module

    qwen_utils_module = types.ModuleType("qwen_vl_utils")
    vision_process_module = types.ModuleType("qwen_vl_utils.vision_process")
    vision_process_module.process_vision_info = lambda *args, **kwargs: (None, None, {})

    transformers_module = types.ModuleType("transformers")
    transformers_module.AutoProcessor = type("AutoProcessor", (), {})
    transformers_module.BitsAndBytesConfig = type(
        "BitsAndBytesConfig",
        (),
        {"__init__": lambda self, **kwargs: setattr(self, "kwargs", kwargs)},
    )
    transformers_module.Qwen3VLForConditionalGeneration = type("Qwen3VLForConditionalGeneration", (), {})

    cache_utils_module = types.ModuleType("transformers.cache_utils")
    cache_utils_module.Cache = type("Cache", (), {})

    modeling_outputs_module = types.ModuleType("transformers.modeling_outputs")
    modeling_outputs_module.ModelOutput = type("ModelOutput", (), {})

    modeling_qwen3_vl_module = types.ModuleType("transformers.models.qwen3_vl.modeling_qwen3_vl")
    modeling_qwen3_vl_module.Qwen3VLConfig = type("Qwen3VLConfig", (), {})
    modeling_qwen3_vl_module.Qwen3VLModel = type("Qwen3VLModel", (), {})
    modeling_qwen3_vl_module.Qwen3VLPreTrainedModel = type("Qwen3VLPreTrainedModel", (), {})

    processing_qwen3_vl_module = types.ModuleType("transformers.models.qwen3_vl.processing_qwen3_vl")
    processing_qwen3_vl_module.Qwen3VLProcessor = type("Qwen3VLProcessor", (), {})

    processing_utils_module = types.ModuleType("transformers.processing_utils")
    processing_utils_module.Unpack = type("Unpack", (), {"__class_getitem__": classmethod(lambda cls, item: cls)})

    utils_module = types.ModuleType("transformers.utils")
    utils_module.TransformersKwargs = type("TransformersKwargs", (), {})

    stubbed_modules = {
        "torch": torch_module,
        "torch.nn": torch_nn_module,
        "torch.nn.functional": functional_module,
        "PIL": pil_module,
        "PIL.Image": image_module,
        "qwen_vl_utils": qwen_utils_module,
        "qwen_vl_utils.vision_process": vision_process_module,
        "transformers": transformers_module,
        "transformers.cache_utils": cache_utils_module,
        "transformers.modeling_outputs": modeling_outputs_module,
        "transformers.models.qwen3_vl.modeling_qwen3_vl": modeling_qwen3_vl_module,
        "transformers.models.qwen3_vl.processing_qwen3_vl": processing_qwen3_vl_module,
        "transformers.processing_utils": processing_utils_module,
        "transformers.utils": utils_module,
    }

    with mock.patch.dict(sys.modules, stubbed_modules, clear=False):
        spec = importlib.util.spec_from_file_location("test_qwen3_wrappers", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class Qwen3VLRerankerProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_wrappers_module()

    def test_process_batches_conversations_before_tokenize(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.default_instruction = "instruction"
        reranker.fps = 1
        reranker.max_frames = 64
        reranker.model = types.SimpleNamespace(device="cpu")

        pair_sentinel_1 = object()
        pair_sentinel_2 = object()
        pair_sentinels = [pair_sentinel_1, pair_sentinel_2]
        tokenize_calls = []

        reranker.format_mm_instruction = lambda *args, **kwargs: pair_sentinels.pop(0)
        reranker.tokenize = lambda pair: tokenize_calls.append(pair) or DummyInputs()
        reranker.compute_scores = lambda inputs: [0.91, 0.37]

        result = self.module.Qwen3VLReranker.process(reranker, {
            "query": {"image": "/tmp/query.jpg"},
            "documents": [
                {"text": "红烧肉", "image": "/tmp/doc-1.jpg"},
                {"text": "番茄炒蛋", "image": "/tmp/doc-2.jpg"},
            ],
        })

        self.assertEqual(tokenize_calls, [[pair_sentinel_1, pair_sentinel_2]])
        self.assertEqual(result, [0.91, 0.37])

    def test_preferred_model_dtype_uses_bfloat16_when_supported(self):
        device = types.SimpleNamespace(type="cuda")
        with mock.patch.object(self.module.torch.cuda, "is_bf16_supported", return_value=True):
            dtype = self.module._preferred_model_dtype(device)

        self.assertEqual(dtype, self.module.torch.bfloat16)

    def test_preferred_model_dtype_falls_back_to_float16(self):
        device = types.SimpleNamespace(type="cuda")
        with mock.patch.object(self.module.torch.cuda, "is_bf16_supported", return_value=False):
            dtype = self.module._preferred_model_dtype(device)

        self.assertEqual(dtype, self.module.torch.float16)

    def test_int8_loading_uses_bitsandbytes_device_map(self):
        device = types.SimpleNamespace(type="cuda", index=None)
        kwargs = {}

        quantized = self.module._configure_model_loading(device, "int8", kwargs)

        self.assertTrue(quantized)
        self.assertEqual(kwargs["device_map"], {"": 0})
        self.assertEqual(kwargs["quantization_config"].kwargs, {"load_in_8bit": True})

    def test_tokenize_fallback_keeps_batch_of_one_shape(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.max_length = 16
        reranker._summarize_pairs = lambda pairs: {"count": len(pairs)}
        reranker.truncate_tokens_optimized = lambda tokens, max_length, special_tokens: tokens
        reranker.processor = FakeProcessor()

        with mock.patch.object(self.module, "process_vision_info", side_effect=RuntimeError("bad image")):
            inputs = self.module.Qwen3VLReranker.tokenize(reranker, [[{
                "role": "user",
                "content": [{"type": "image", "image": "file:///tmp/bad.jpg"}],
            }]])

        self.assertEqual(reranker.processor.chat_template_calls[0]["pairs_len"], 1)
        self.assertEqual(reranker.processor.chat_template_calls[1]["pairs_len"], 1)
        self.assertEqual(reranker.processor.chat_template_calls[1]["first_item_type"], "list")
        self.assertEqual(inputs["input_ids"], [[101, 102, 103]])

    def test_tokenize_pads_token_aligned_multimodal_fields(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.max_length = 16
        reranker._summarize_pairs = lambda pairs: {"count": len(pairs)}
        reranker.processor = FakeVariableLengthProcessor()

        inputs = self.module.Qwen3VLReranker.tokenize(reranker, [
            [{"role": "user", "content": [{"type": "text", "text": "short"}]}],
            [{"role": "user", "content": [{"type": "text", "text": "longer"}]}],
        ])

        self.assertEqual(inputs["input_ids"], [
            [0, 101, 102],
            [201, 202, 203],
        ])
        self.assertEqual(inputs["mm_token_type_ids"], [
            [0, 7, 7],
            [8, 8, 8],
        ])
        self.assertEqual(inputs["token_type_ids"], [
            [0, 1, 1],
            [2, 2, 2],
        ])

    def test_process_passes_max_frames_to_instruction_builder(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.default_instruction = "instruction"
        reranker.fps = 1
        reranker.max_frames = 64
        reranker.model = types.SimpleNamespace(device="cpu")

        captured = {}
        pair_sentinel = object()

        def fake_format_mm_instruction(*args, **kwargs):
            captured["max_frames"] = kwargs.get("max_frames")
            return pair_sentinel

        reranker.format_mm_instruction = fake_format_mm_instruction
        tokenize_calls = []
        reranker.tokenize = lambda pairs: tokenize_calls.append(pairs) or DummyInputs()
        reranker.compute_scores = lambda inputs: [0.5]

        self.module.Qwen3VLReranker.process(reranker, {
            "query": {"image": "/tmp/query.jpg"},
            "documents": [{"text": "红烧肉", "image": "/tmp/doc.jpg"}],
            "max_frames": 12,
        })

        self.assertEqual(captured["max_frames"], 12)
        self.assertEqual(len(tokenize_calls), 1)

    def test_format_mm_content_rejects_unknown_image_type(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.min_pixels = 1
        reranker.max_pixels = 2

        with self.assertRaises(TypeError):
            self.module.Qwen3VLReranker.format_mm_content(
                reranker,
                text=None,
                image=object(),
                video=None,
            )

    def test_normalize_model_inputs_casts_tensor_like_keys(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.model = types.SimpleNamespace(device="cpu")

        normalized = self.module.Qwen3VLReranker._normalize_model_inputs(reranker, {
            "input_ids": [[1, 2, 3]],
            "attention_mask": [[1, 1, 1]],
            "mm_token_type_ids": [[0, 1, 1]],
            "meta": "keep",
        })

        self.assertEqual(normalized["input_ids"].data, [[1, 2, 3]])
        self.assertEqual(normalized["attention_mask"].data, [[1, 1, 1]])
        self.assertEqual(normalized["mm_token_type_ids"].data, [[0, 1, 1]])
        self.assertEqual(normalized["meta"], "keep")


class DummyInputs:
    def to(self, _device):
        return self


class FakeTensor:
    def __init__(self, data, device=None):
        self.data = data
        self.device = device
        self.shape = ()

    def to(self, device):
        self.device = device
        return self


class FakeProcessor:
    def __init__(self):
        self.chat_template_calls = []
        self.tokenizer = FakeTokenizer()

    def apply_chat_template(self, pairs, tokenize=False, add_generation_prompt=True):
        self.chat_template_calls.append({
            "pairs_len": len(pairs),
            "first_item_type": type(pairs[0]).__name__ if pairs else None,
        })
        return "template"

    def __call__(self, **kwargs):
        return {"input_ids": [[101, 102, 103]]}


class FakeTokenizer:
    all_special_ids = []
    padding_side = "right"

    def pad(self, payload, padding=True, return_tensors="pt", max_length=None):
        return {"input_ids": payload["input_ids"]}


class FakeVariableLengthProcessor:
    def __init__(self):
        self.tokenizer = FakeLeftPaddingTokenizer()

    def apply_chat_template(self, pairs, tokenize=False, add_generation_prompt=True):
        return "template"

    def __call__(self, **kwargs):
        return {
            "input_ids": [
                [101, 102],
                [201, 202, 203],
            ],
            "mm_token_type_ids": [
                [7, 7],
                [8, 8, 8],
            ],
            "token_type_ids": [
                [1, 1],
                [2, 2, 2],
            ],
        }


class FakeLeftPaddingTokenizer:
    all_special_ids = []
    padding_side = "left"

    def pad(self, payload, padding=True, return_tensors="pt", max_length=None):
        rows = payload["input_ids"]
        target_length = max(len(row) for row in rows)
        return {
            "input_ids": [
                [0] * (target_length - len(row)) + row
                for row in rows
            ]
        }


if __name__ == "__main__":
    unittest.main()
