from __future__ import annotations

import unittest

from vibecoder.pydantic_compat import BaseModel, Field, IgnoreExtraBaseModel


class StrictExample(BaseModel):
    name: str = Field(min_length=1)


class RemoteExample(IgnoreExtraBaseModel):
    identifier: int


class PydanticCompatibilityTests(unittest.TestCase):
    def test_v1_v2_surface_and_strict_extra_policy(self):
        value = StrictExample.model_validate({"name": "demo"})
        self.assertEqual(value.model_dump(mode="json"), {"name": "demo"})
        self.assertEqual(value.model_copy(update={"name": "copy"}).name, "copy")
        with self.assertRaises(Exception):
            StrictExample.model_validate({"name": "demo", "unexpected": True})

    def test_remote_models_ignore_unknown_api_fields(self):
        value = RemoteExample.model_validate({"identifier": 7, "future": "field"})
        self.assertEqual(value.model_dump(), {"identifier": 7})


if __name__ == "__main__":
    unittest.main()
