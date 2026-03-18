# coding=utf-8
"""Helpers for detecting upload actions from OctoPrint requests and payloads."""


class UploadIntentService:
    """Normalizes immediate select/print flags across request and event payload shapes."""

    TRUTHY_VALUES = {"1", "true", "on", "yes"}
    IMMEDIATE_ACTION_KEYS = (
        "print",
        "select",
        "effective_print",
        "effective_select",
    )
    IMMEDIATE_PRINT_KEYS = (
        "print",
        "effective_print",
    )

    @classmethod
    def wants_immediate_select_or_print(cls, values):
        """Return whether the request/payload asks OctoPrint to select or print immediately."""
        return any(cls.flag_enabled(values, key) for key in cls.IMMEDIATE_ACTION_KEYS)

    @classmethod
    def wants_immediate_print(cls, values):
        """Return whether the request/payload asks OctoPrint to begin printing immediately."""
        return any(cls.flag_enabled(values, key) for key in cls.IMMEDIATE_PRINT_KEYS)

    @classmethod
    def flag_enabled(cls, values, key):
        """Read a boolean-ish flag from either snake_case or camelCase keys."""
        if not values:
            return False

        for candidate in (key, cls._camel_case(key)):
            value = cls._lookup(values, candidate)
            if value is None:
                continue
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in cls.TRUTHY_VALUES
            return str(value).strip().lower() in cls.TRUTHY_VALUES
        return False

    @staticmethod
    def _lookup(values, key):
        try:
            if key in values:
                return values.get(key)
        except TypeError:
            return None
        return None

    @staticmethod
    def _camel_case(key):
        parts = key.split("_")
        return "".join(
            part.capitalize() if index else part for index, part in enumerate(parts)
        )
