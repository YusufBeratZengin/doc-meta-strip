"""Format handlers."""

from __future__ import annotations

from ..base import Handler
from .image_handler import ImageHandler
from .office_handler import OfficeHandler
from .pdf_handler import PdfHandler

__all__ = ["HANDLERS", "ImageHandler", "OfficeHandler", "PdfHandler"]

#: Handlers are stateless, so a single shared instance per format is enough.
HANDLERS: tuple[Handler, ...] = (ImageHandler(), OfficeHandler(), PdfHandler())
