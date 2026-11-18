"""The fake backend lives in the package (``mooring --fake`` uses it); re-exported for tests."""

from mooring.backend.fake import PHONE_ID, TABLET_ID, FakeBackend

__all__ = ["PHONE_ID", "TABLET_ID", "FakeBackend"]
