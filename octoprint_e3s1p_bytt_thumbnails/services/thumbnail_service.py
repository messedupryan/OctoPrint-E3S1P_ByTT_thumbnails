# coding=utf-8
"""Thumbnail extraction helpers."""

import base64
import io
import os
import re

import octoprint.util
from PIL import Image


class ThumbnailService:
    """Extracts embedded thumbnails from supported G-code files."""

    THUMBNAIL_CONTENT_REGEX = re.compile(
        r"(?:^; (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)"
        r"([\s\S]*?)"
        r"(?:^; (?:thumbnail(?:_JPG)*|jpg) end|\Z)",
        re.MULTILINE,
    )

    def __init__(self, logger):
        self._logger = logger

    def extract_thumbnail(self, gcode_filename, thumbnail_filename):
        """Extract the largest embedded thumbnail to a JPEG file."""
        self._logger.debug(
            f"Extracting thumbnail from {gcode_filename} to {thumbnail_filename}"
        )
        try:
            collected = self._read_header(gcode_filename)
            matches = self.THUMBNAIL_CONTENT_REGEX.findall(collected)
            if not matches:
                self._logger.debug(
                    f"No embedded thumbnail markers found in {gcode_filename}"
                )
                return False

            chosen = max(matches, key=len)
            path = os.path.dirname(thumbnail_filename)
            if path and not os.path.exists(path):
                os.makedirs(path)

            with open(thumbnail_filename, "wb") as jpg_file:
                jpg_file.write(self._decode_thumbnail(chosen))

            self._logger.debug(
                f"Thumbnail extracted successfully to {thumbnail_filename}"
            )
            return True
        except Exception as exc:
            self._logger.error(
                f"Thumbnail extraction failed for {gcode_filename} -> {thumbnail_filename}: {exc}",
                exc_info=True,
            )
            return False

    def has_embedded_thumbnail(self, gcode_filename):
        """Return whether the file contains a supported embedded thumbnail."""
        try:
            collected = self._read_header(gcode_filename)
            has_thumbnail = bool(self.THUMBNAIL_CONTENT_REGEX.search(collected))
            self._logger.debug(
                f"has_embedded_thumbnail({gcode_filename}) -> {has_thumbnail}"
            )
            return has_thumbnail
        except Exception as exc:
            self._logger.error(
                f"Failed checking embedded thumbnail for {gcode_filename}: {exc}",
                exc_info=True,
            )
            return False

    def _read_header(self, gcode_filename):
        collected = ""
        with open(gcode_filename, "r", encoding="utf8", errors="ignore") as gcode_file:
            for line_num, line in enumerate(gcode_file, start=1):
                gcode = octoprint.util.comm.gcode_command_for_cmd(line)
                extrusion_match = octoprint.util.comm.regexes_parameters[
                    "floatE"
                ].search(line)
                if gcode == "G1" and extrusion_match:
                    self._logger.debug(
                        f"Line {line_num}: detected first extrusion, stopping header scan."
                    )
                    break
                if (
                    line.startswith(";")
                    or line.startswith("\n")
                    or line.startswith("M10086 ;")
                    or line[0:4] in ["W220", "W221", "W222"]
                ):
                    collected += line

        normalized = collected.replace(
            octoprint.util.to_unicode("\r\n"),
            octoprint.util.to_unicode("\n"),
        )
        return normalized.replace(
            octoprint.util.to_unicode(";\n;\n"),
            octoprint.util.to_unicode(";\n\n;\n"),
        )

    def _decode_thumbnail(self, match):
        encoded_jpg = base64.b64decode(match.replace("; ", "").encode())
        with io.BytesIO(encoded_jpg) as jpg_bytes:
            image = Image.open(jpg_bytes)
            return self._image_to_jpg(image)

    @staticmethod
    def _image_to_jpg(image):
        with io.BytesIO() as jpg_bytes:
            image.save(jpg_bytes, "JPEG")
            return jpg_bytes.getvalue()
