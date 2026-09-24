from __future__ import annotations

_SENTENCE_ENDINGS: tuple[str, ...] = (".", "!", "?")


def end_sentence(text: str) -> str:
    """``text`` with a full stop added, unless it already ends a sentence.

    Wording this package appends goes after text it did not write: a consumer's
    ``QueryParam`` description, a serializer's error message. Neither reliably
    ends in punctuation (a field-selection library's "`items` field is not
    found" does not, DRF's own "This field is required." does), and a sentence
    appended to one that has not ended reads as a run-on to the model it is
    written for.
    """
    stripped = text.rstrip()
    return stripped if stripped.endswith(_SENTENCE_ENDINGS) else f"{stripped}."


__all__ = ["end_sentence"]
