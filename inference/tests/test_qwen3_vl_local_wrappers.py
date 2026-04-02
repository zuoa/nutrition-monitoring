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
    torch_module.Tensor = object
    torch_module.FloatTensor = object
    torch_module.LongTensor = object
    torch_module.device = lambda value: value
    torch_module.no_grad = lambda: (lambda fn: fn)
    torch_module.sigmoid = lambda value: value
    torch_module.cuda = types.SimpleNamespace(is_available=lambda: False)
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

    def test_process_passes_single_conversation_to_tokenize(self):
        reranker = object.__new__(self.module.Qwen3VLReranker)
        reranker.default_instruction = "instruction"
        reranker.fps = 1
        reranker.model = types.SimpleNamespace(device="cpu")

        pair_sentinel = object()
        tokenize_calls = []

        reranker.format_mm_instruction = lambda *args, **kwargs: pair_sentinel
        reranker.tokenize = lambda pair: tokenize_calls.append(pair) or DummyInputs()
        reranker.compute_scores = lambda inputs: [0.91]

        result = self.module.Qwen3VLReranker.process(reranker, {
            "query": {"image": "/tmp/query.jpg"},
            "documents": [{"text": "红烧肉", "image": "/tmp/doc.jpg"}],
        })

        self.assertEqual(tokenize_calls, [pair_sentinel])
        self.assertEqual(result, [0.91])


class DummyInputs:
    def to(self, _device):
        return self


if __name__ == "__main__":
    unittest.main()
