import unittest
from types import SimpleNamespace

import numpy as np

from app.services.visual_embedding import VisualEmbeddingIndexService, VisualFeatureExtractor


class VisualEmbeddingTests(unittest.TestCase):
    def test_global_fusion_normalization_is_unit_length(self):
        normalized = VisualFeatureExtractor._normalize(np.asarray([3.0, 4.0], dtype=np.float32))
        self.assertAlmostEqual(float(np.linalg.norm(normalized)), 1.0, places=6)

    def test_bidirectional_maxsim_rewards_matching_patches(self):
        query = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        matching = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        unrelated = np.asarray([[-1.0, 0.0], [0.0, -1.0]], dtype=np.float32)
        self.assertGreater(
            VisualEmbeddingIndexService._maxsim(query, matching),
            VisualEmbeddingIndexService._maxsim(query, unrelated),
        )

    def test_raw_maxsim_threshold_is_transformed_to_confidence_space(self):
        self.assertAlmostEqual(VisualEmbeddingIndexService._confidence_from_maxsim(0.5), 0.75)

    def test_timm_dinov3_removes_all_five_prefix_tokens(self):
        class FakeModel:
            config = SimpleNamespace()
            timm_model = SimpleNamespace(patch_embed=SimpleNamespace(patch_size=(16, 16)))

            def __init__(self):
                self.forward_args = None

            def __call__(self, **kwargs):
                self.forward_args = kwargs
                hidden = np.arange(261, dtype=np.float32).reshape(1, 261, 1)
                return SimpleNamespace(last_hidden_state=hidden)

        model = FakeModel()
        pixels = np.zeros((1, 3, 256, 256), dtype=np.float32)
        patches = VisualFeatureExtractor._extract_dino_patch_tokens(model, {"pixel_values": pixels})

        self.assertFalse(model.forward_args["do_pooling"])
        self.assertEqual(patches.shape, (1, 256, 1))
        self.assertEqual(float(patches[0, 0, 0]), 5.0)


if __name__ == "__main__":
    unittest.main()
