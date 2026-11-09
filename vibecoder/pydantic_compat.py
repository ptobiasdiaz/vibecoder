"""Small Pydantic 1/2 compatibility surface used by the packaged runtime."""

from __future__ import annotations

from typing import Any

import pydantic
from pydantic import Field as _PydanticField, ValidationError


PYDANTIC_V2 = hasattr(pydantic.BaseModel, "model_validate")
_MISSING = object()


def Field(default: Any = _MISSING, **kwargs: Any):
    """Normalize the few renamed constraints used by both supported majors."""
    if PYDANTIC_V2 and "min_items" in kwargs:
        kwargs["min_length"] = kwargs.pop("min_items")
    if not PYDANTIC_V2 and "pattern" in kwargs:
        kwargs["regex"] = kwargs.pop("pattern")
    if default is _MISSING:
        return _PydanticField(**kwargs)
    return _PydanticField(default, **kwargs)

if PYDANTIC_V2:
    from pydantic import ConfigDict, field_validator

    class BaseModel(pydantic.BaseModel):
        model_config = ConfigDict(extra="forbid")

    class IgnoreExtraBaseModel(pydantic.BaseModel):
        model_config = ConfigDict(extra="ignore")

    class StripBaseModel(pydantic.BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
else:
    from pydantic import validator

    def field_validator(*fields: str, mode: str = "after", **kwargs: Any):
        kwargs.setdefault("allow_reuse", True)
        return validator(*fields, pre=mode == "before", **kwargs)

    class _V1Methods(pydantic.BaseModel):
        @classmethod
        def model_validate(cls, value: Any):
            return cls.parse_obj(value)

        @classmethod
        def model_validate_json(cls, value: str | bytes):
            return cls.parse_raw(value)

        def model_dump(self, *, mode: str | None = None, **kwargs: Any) -> dict[str, Any]:
            del mode
            return self.dict(**kwargs)

        def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False):
            return self.copy(update=update, deep=deep)

    class BaseModel(_V1Methods):
        class Config:
            extra = "forbid"

    class IgnoreExtraBaseModel(_V1Methods):
        class Config:
            extra = "ignore"

    class StripBaseModel(_V1Methods):
        class Config:
            extra = "forbid"
            anystr_strip_whitespace = True


__all__ = [
    "BaseModel", "Field", "IgnoreExtraBaseModel", "PYDANTIC_V2",
    "StripBaseModel", "ValidationError", "field_validator",
]
