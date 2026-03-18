# coding=utf-8
"""Helpers for generating the E3S1/ByTT transfer file."""

import os
import re

import octoprint.util


class HelperFileService:
    """Builds and filters the helper G-code file sent to printer SD."""

    HELPER_CONTENT_REGEX = re.compile(
        r"(^M4010 (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)"
        r"([\s\S]*?)"
        r"(^M4010 (?:thumbnail(?:_JPG)*|jpg) end|\Z)",
        re.MULTILINE,
    )
    HELPER_LINE_REGEX = re.compile(r"^\s*M4010\b")

    def __init__(self, logger):
        self._logger = logger

    def filter_helper_file(self, path):
        """Keep only M4010 lines in the helper file."""
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as file_handle:
                source_lines = file_handle.readlines()
            kept_lines = [
                line for line in source_lines if self.HELPER_LINE_REGEX.match(line)
            ]
            if len(kept_lines) != len(source_lines):
                with open(path, "w", encoding="utf-8") as output_handle:
                    output_handle.writelines(kept_lines)
                self._logger.debug(
                    f"Filtered {len(source_lines) - len(kept_lines)} non-M4010 line(s) from {path}"
                )
        except Exception as exc:
            self._logger.error(f"Filtering helper failed for {path}: {exc}")

    def extract_transfer_file(self, gcode_filename, helper_filename):
        """Extract helper commands from a selected G-code file."""
        self._logger.debug(
            f"Extracting helper transfer file from {gcode_filename} to {helper_filename}"
        )
        try:
            collected = ""
            replace_next_line = False

            with open(
                gcode_filename, "r", encoding="utf8", errors="ignore"
            ) as gcode_file:
                for line_num, line in enumerate(gcode_file, start=1):
                    gcode = octoprint.util.comm.gcode_command_for_cmd(line)
                    extrusion_match = octoprint.util.comm.regexes_parameters[
                        "floatE"
                    ].search(line)
                    if line.startswith(";"):
                        replace_next_line = True
                    if replace_next_line:
                        line = "M4010" + line[1:]
                        replace_next_line = False
                    collected += line
                    if gcode == "G1" and extrusion_match:
                        self._logger.debug(
                            f"Line {line_num}: detected first extrusion, stopping helper scan."
                        )
                        break

            normalized = collected.replace(
                octoprint.util.to_unicode("\r\n"),
                octoprint.util.to_unicode("\n"),
            )
            normalized = normalized.replace(
                octoprint.util.to_unicode(";\n;\n"),
                octoprint.util.to_unicode(";\n\n;\n"),
            )
            matches = self.HELPER_CONTENT_REGEX.findall(normalized)
            if not matches:
                self._logger.debug(f"No helper M4010 content found in {gcode_filename}")
                return False

            helper_dir = os.path.dirname(helper_filename)
            if helper_dir and not os.path.exists(helper_dir):
                os.makedirs(helper_dir)

            with open(helper_filename, "w", encoding="utf-8") as file_handle:
                for line_tuple in matches:
                    blob = "".join(line_tuple)
                    for raw in blob.splitlines():
                        if self.HELPER_LINE_REGEX.match(raw):
                            file_handle.write(raw.rstrip() + "\n")

            self.filter_helper_file(helper_filename)
            self._logger.debug(f"Helper transfer file written to {helper_filename}")
            return True
        except Exception as exc:
            self._logger.error(
                f"Helper transfer extraction failed for {gcode_filename} -> {helper_filename}: {exc}",
                exc_info=True,
            )
            return False
