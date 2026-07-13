import unittest

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


if __name__ == "__main__":
    unittest.main()
