import importlib.util
import os
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "model_management.py",
)
SPEC = importlib.util.spec_from_file_location("model_management", MODULE_PATH)
MODEL_MANAGEMENT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODEL_MANAGEMENT)


class ModelManagementTests(unittest.TestCase):
    def test_unknown_mode_falls_back_to_local(self):
        self.assertEqual(
            MODEL_MANAGEMENT.normalize_local_model_management_mode("remote"),
            MODEL_MANAGEMENT.LOCAL_MODEL_MANAGEMENT_MODE_LOCAL,
        )

    def test_retrieval_api_mode_is_recognized(self):
        self.assertTrue(
            MODEL_MANAGEMENT.is_retrieval_api_model_management(
                {"LOCAL_MODEL_MANAGEMENT_MODE": "retrieval_api"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
