# coding=utf-8
from __future__ import absolute_import
from __future__ import unicode_literals

import flask
import octoprint.plugin
import octoprint.filemanager
import octoprint.filemanager.util
import octoprint.util
import os
import datetime
import io
from PIL import Image
import re
import base64
import imghdr
import requests
import sys
import socket
import octoprint.events
import octoprint.printer
import urllib.request
import email
import random
import string
import json
import time

from octoprint.server import printer, fileManager, slicingManager, eventManager, NO_CONTENT
from octoprint.events import Events, eventManager
from octoprint.plugin import OctoPrintPlugin
from flask_babel import gettext
from octoprint.access import ADMIN_GROUP
from octoprint.access.permissions import Permissions
from octoprint.filemanager.destinations import FileDestinations

from octoprint.settings import settings

try:
    from urllib import quote, unquote
except ImportError:
    from urllib.parse import quote, unquote

timeout = 15
socket.setdefaulttimeout(timeout)

# === Single source of truth for the helper filename ===
HELPER_BASENAME = "OCTODGUS.GCO"


class E3S1PROFORKBYTTThumbnailsPlugin(octoprint.plugin.SettingsPlugin,
                                       octoprint.plugin.AssetPlugin,
                                       octoprint.plugin.TemplatePlugin,
                                       octoprint.plugin.EventHandlerPlugin,
                                       octoprint.plugin.StartupPlugin,
                                       octoprint.printer.PrinterCallback,
                                       octoprint.plugin.SimpleApiPlugin):

    def __init__(self):
        self.file_scanner = None
        self.syncing = False
        self._fileRemovalTimer = None
        self._fileRemovalLastDeleted = None
        self._fileRemovalLastAdded = None
        self._folderRemovalTimer = None
        self._folderRemovalLastDeleted = {}
        self._folderRemovalLastAdded = {}
        self._waitForAnalysis = False
        self._analysis_active = False
        self._plugin_version = "2.1.0"
        self.regex_extension = re.compile(r"\.(?:gco(?:de)?|tft)$")
        self.use_e3s1proforkbytt = False
        self.gcodeExt = "gcode"
        self.api_key = None
        self.hostIP = "127.0.0.1"
        self.octoPort = "5000"
        self.sslBool = "no"  # default
        self.sendLoc = "sdcard"  # default
        self.printBool = "false"
        self.selectBool = "false"
        self.selectedPrintFilename = "None"
        self.selectedPrintFileFoldername = "None"
        self.selectedPrintFolderRel = ""
        self.octodgusFilename = "None"  # absolute path to plugin-data helper

        # loop control
        self._active_helper_inflight = False
        self._suppress_next_file_selected_name = None

    # ~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        return {
            'installed': True,
            'inline_thumbnail': False,
            'scale_inline_thumbnail': False,
            'inline_thumbnail_scale_value': "50",
            'inline_thumbnail_position_left': False,
            'align_inline_thumbnail': False,
            'inline_thumbnail_align_value': "left",
            'state_panel_thumbnail': True,
            'state_panel_thumbnail_scale_value': "100",
            'resize_filelist': False,
            'filelist_height': "306",
            'scale_inline_thumbnail_position': False,
            'sync_on_refresh': False,
            'use_uploads_folder': True,
            'relocate_progress': False,
            'inline_thumbnail_uploadmanager': True,
            'api_key': "NOAPIKEY"
        }

    # ~~ Get current API key from settings

    def _current_api_key(self):
        # Always read the newest value from settings; also cache it
        key = self._settings.get(["api_key"])
        if key and key != "NOAPIKEY":
            self.api_key = key
        return self.api_key

    # ~~ Update API key on settings save

    def on_settings_save(self, data):
        super(E3S1PROFORKBYTTThumbnailsPlugin, self).on_settings_save(data)
        # Pull the fresh key right after saving
        self.api_key = self._settings.get(["api_key"])
        self._logger.info("E3S1PROFORKBYTT: API key updated from settings without restart.")

    # ~~ AssetPlugin mixin

    def get_assets(self):
        return {
            'js': ["js/e3s1proforkbyttthumbnails.js"],
            'css': ["css/e3s1proforkbyttthumbnails.css"]
        }

    # ~~ TemplatePlugin mixin

    def get_template_configs(self):
        return [
            {'type': "settings", 'custom_bindings': False, 'template': "e3s1proforkbyttthumbnails_settings.jinja2"},
        ]

    # ===== helper hardening =====

    def generate_boundary(self):
        return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(30))

    def _filter_helper_file(self, path):
        """Keep only M4010 lines; drop everything else (e.g. M118 from other plugins)."""
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                src = fh.readlines()
            kept = [l for l in src if re.match(r'^\s*M4010\b', l)]
            if len(kept) != len(src):
                with open(path, "w", encoding="utf-8") as out:
                    out.writelines(kept)
                self._logger.debug("Filtered %d non-M4010 line(s) from %s", len(src) - len(kept), path)
        except Exception as e:
            self._logger.error("Filtering helper failed for %s: %s", path, e)

    def _purge_uploads_helper(self, hint_rel_path=None):
        """
        Delete any uploads/OCTODGUS.GCO (case-insensitive) that OctoPrint may have staged.
        Use AFTER SD transfer completes or when analysis kicks in on it.
        """
        try:
            # 1) Try file_manager with the provided relative path
            if hint_rel_path:
                try:
                    if os.path.basename(hint_rel_path).upper() == HELPER_BASENAME:
                        self._file_manager.remove_file("local", hint_rel_path)
                        self._logger.debug("Removed helper via file_manager: %s", hint_rel_path)
                        return
                except Exception as e:
                    self._logger.debug("file_manager.remove_file failed for %s: %s", hint_rel_path, e)

            # 2) Try HTTP API (keeps list in sync)
            try:
                url = f"http://{self.hostIP}:{self.octoPort}/api/files/local/{HELPER_BASENAME}"
                headers = {'User-agent': 'Cura AutoUploader Plugin', 'X-Api-Key': self.api_key}
                r = requests.delete(url, headers=headers, timeout=5)
                if r.status_code in (204, 404):
                    self._logger.debug("DELETE /api/files/local/%s -> %s", HELPER_BASENAME, r.status_code)
                    if r.status_code == 204:
                        return
            except Exception:
                pass

            # 3) Fallback: direct disk scan/removal
            uploads_root = self._file_manager.path_on_disk("local", "")
            for entry in os.listdir(uploads_root):
                if entry.upper() == HELPER_BASENAME:
                    try:
                        os.remove(os.path.join(uploads_root, entry))
                        self._logger.debug("Removed uploads/%s from disk", entry)
                    except Exception as e:
                        self._logger.debug("Failed removing uploads/%s from disk: %s", entry, e)
        except Exception as e:
            self._logger.debug("Purge uploads helper failed: %s", e)

    def _delete_local_octodgus(self):
        """Remove any helper variants in uploads: OCTODGUS.GCO (any case)."""
        try:
            # Try HTTP API delete first
            try:
                url = f"http://{self.hostIP}:{self.octoPort}/api/files/local/{HELPER_BASENAME}"
                headers = {'User-agent': 'Cura AutoUploader Plugin', 'X-Api-Key': self._current_api_key()}
                requests.delete(url, headers=headers, timeout=5)
            except Exception:
                pass

            # Also remove by filesystem in case not indexed
            root = self._file_manager.path_on_disk("local", "")
            for entry in os.listdir(root):
                if entry.upper() == HELPER_BASENAME:
                    try:
                        os.remove(os.path.join(root, entry))
                        self._logger.debug("Removed uploads/%s", entry)
                    except Exception as e:
                        self._logger.debug("Failed removing uploads/%s: %s", entry, e)
        except Exception as e:
            self._logger.debug("Local helper cleanup failed: %s", e)

    def delete_existing_file(self, filename, file_location):
        self._logger.debug("E3S1PROFORKBYTT delete_existing_file is %s.", filename)
        if file_location == "sdcard":
            url = f"http://{self.hostIP}:{self.octoPort}/api/files/sdcard/{filename}"
        else:
            url = f"http://{self.hostIP}:{self.octoPort}/api/files/local/{filename}"
        headers = {'User-agent': 'Cura AutoUploader Plugin', 'X-Api-Key': self._current_api_key()}
        try:
            response = requests.delete(url, headers=headers, timeout=10)
            if response.status_code == 204:
                self._logger.debug("Deleted %s on %s.", filename, file_location)
        except requests.exceptions.RequestException as e:
            self._logger.debug("Delete error for %s on %s: %s", filename, file_location, e)

    # ===== upload to PRINTER SD as OCTODGUS.GCO =====
    def send_file(self, filename, gcode_filename):
        outputName = HELPER_BASENAME
        protocol = "https://" if self.sslBool == "yes" else "http://"
        url = protocol + self.hostIP + ":" + self.octoPort + "/api/files/sdcard"

        # normalize flags
        self.selectBool = 'no'
        self.printBool = 'no'

        self._logger.debug("Uploading helper to PRINTER SD as %s -> %s", outputName, url)

        try:
            filename_on_disk = filename if os.path.isabs(filename) else self._file_manager.path_on_disk("local", filename)

            # Filter just before upload
            self._filter_helper_file(filename_on_disk)

            with open(filename_on_disk, 'rb') as fh:
                filebody = fh.read()

            mimetype = 'application/octet-stream'
            boundary = self.generate_boundary()
            content_type = 'multipart/form-data; boundary=%s' % boundary
            body_boundary = '--' + boundary

            parts = [
                body_boundary,
                'Content-Disposition: form-data; name="file"; filename="%s"' % outputName,
                'Content-Type: %s' % mimetype,
                '',
                filebody.decode('utf-8', errors='ignore'),
                body_boundary,
                'Content-Disposition: form-data; name="select"',
                '',
                self.selectBool,
                body_boundary,
                'Content-Disposition: form-data; name="print"',
                '',
                self.printBool,
                body_boundary + '--',
                ''
            ]
            body = '\r\n'.join(parts)
            bytes_body = body.encode('utf-8')

            req = urllib.request.Request(url)
            req.add_header('User-agent', 'Cura AutoUploader Plugin')
            req.add_header('Content-type', content_type)
            req.add_header('Content-length', str(len(bytes_body)))
            req.add_header('X-Api-Key', self._current_api_key())
            req.data = bytes_body

            with urllib.request.urlopen(req) as response:
                rsp = response.read().decode('utf-8', errors='ignore')
                self._logger.debug("Upload response: %s", rsp)

            self._logger.debug("Helper upload done (prepare_file source: %s)", gcode_filename)

        except Exception as e:
            self._logger.error("Failed to upload helper to SD %s: %s", url, e)

    def prepare_file(self, infile, gcode_filename):
        if not os.path.isabs(infile):
            infile = os.path.abspath(infile)
        plugin_data_gcode_path = os.path.join(self.get_plugin_data_folder(), HELPER_BASENAME)
        self._logger.debug("E3S1PROFORKBYTT prepare_file1 is: %s", plugin_data_gcode_path)
        self._logger.debug("E3S1PROFORKBYTT prepare_file2 is: %s", HELPER_BASENAME)
        self._logger.debug("E3S1PROFORKBYTT prepare_file gcode_filename is %s.", gcode_filename)

        # clean any LOCAL uploads helper that other plugins may create
        self._delete_local_octodgus()

        # filter to M4010 only before upload
        self._filter_helper_file(plugin_data_gcode_path)

        # mark inflight BEFORE uploading to prevent re-entrancy
        self._active_helper_inflight = True
        self.send_file(plugin_data_gcode_path, gcode_filename)

    # ===== thumbnail extraction =====

    def _extract_thumbnail(self, gcode_filename, thumbnail_filename):
        regex = r"(?:^; thumbnail(?:_JPG)* begin \d+[x ]\d+ \d+$)(?:\n|\r\n?)((?:.+(?:\n|\r\n?))+?)(?:^; thumbnail(?:_JPG)* end)"
        regex_e3s1proforkbytt_content = r"(?:^; (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)([\s\S]*?)(?:^; (?:thumbnail(?:_JPG)*|jpg) end|\Z)"
        regex_e3s1proforkbytt_full = r"(^; (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)([\s\S]*?)(^; (?:thumbnail(?:_JPG)*|jpg) end|\Z)"
        lineNum = 0
        collectedString = ""
        with open(gcode_filename, "r", encoding="utf8", errors="ignore") as gcode_file:
            for line in gcode_file:
                lineNum += 1
                gcode = octoprint.util.comm.gcode_command_for_cmd(line)
                extrusion_match = octoprint.util.comm.regexes_parameters["floatE"].search(line)
                if gcode == "G1" and extrusion_match:
                    self._logger.debug("Line %d: Detected first extrusion. Read complete.", lineNum)
                    break
                if line.startswith(";") or line.startswith("\n") or line.startswith("M10086 ;") or line[0:4] in ["W220", "W221", "W222"]:
                    collectedString += line
            self._logger.debug(collectedString)
            test_str = collectedString.replace(octoprint.util.to_unicode('\r\n'), octoprint.util.to_unicode('\n'))
        test_str = test_str.replace(octoprint.util.to_unicode(';\n;\n'), octoprint.util.to_unicode(';\n\n;\n'))
        matches_e3s1proforkbytt_content = re.findall(regex_e3s1proforkbytt_content, test_str, re.MULTILINE)
        matches_e3s1proforkbytt_full = re.findall(regex_e3s1proforkbytt_full, test_str, re.MULTILINE)
        if len(matches_e3s1proforkbytt_content) > 0:
            self.use_e3s1proforkbytt = True
            maxlen_e3s1proforkbytt = 0
            choosen_e3s1proforkbytt = -1
            for i in range(len(matches_e3s1proforkbytt_content)):
                curlen_e3s1proforkbytt = len(matches_e3s1proforkbytt_content[i])
                if maxlen_e3s1proforkbytt < curlen_e3s1proforkbytt:
                    maxlen_e3s1proforkbytt = curlen_e3s1proforkbytt
                    choosen_e3s1proforkbytt = i
            path = os.path.dirname(thumbnail_filename)
            if not os.path.exists(path):
                os.makedirs(path)
            with open(thumbnail_filename, "wb") as jpg_file:
                jpg_file.write(self._extract_e3s1proforkbytt_thumbnail(matches_e3s1proforkbytt_content[choosen_e3s1proforkbytt]))

    def _extract_transferfile(self, gcode_filename, printer_thumbnail_filename):
        collectedString = ""
        lineNum = 0
        regex_e3s1proforkbytt_final = r"(^M4010 (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)([\s\S]*?)(^M4010 (?:thumbnail(?:_JPG)*|jpg) end|\Z)"
        with open(gcode_filename, "r", encoding="utf8", errors="ignore") as gcode_file:
            replace_next_line = False
            for line in gcode_file:
                lineNum += 1
                gcode = octoprint.util.comm.gcode_command_for_cmd(line)
                extrusion_match = octoprint.util.comm.regexes_parameters["floatE"].search(line)
                if line.startswith(";"):
                    replace_next_line = True
                if replace_next_line:
                    line = "M4010" + line[1:]
                    replace_next_line = False
                collectedString += line
                if gcode == "G1" and extrusion_match:
                    self._logger.debug("Line %d: Detected first extrusion. Read complete.", lineNum)
                    break
            self._logger.debug(collectedString)
            test_str = collectedString.replace(octoprint.util.to_unicode('\r\n'), octoprint.util.to_unicode('\n'))
        test_str = test_str.replace(octoprint.util.to_unicode(';\n;\n'), octoprint.util.to_unicode(';\n\n;\n'))
        matches_e3s1proforkbytt_final = re.findall(regex_e3s1proforkbytt_final, test_str, re.MULTILINE)
        if len(matches_e3s1proforkbytt_final) > 0:
            path = os.path.dirname(printer_thumbnail_filename)
            if not os.path.exists(path):
                os.makedirs(path)
            self._logger.debug("E3S1PROFORKBYTT _extract_transferfile gcode_filename is %s.", gcode_filename)
            self._logger.debug("E3S1PROFORKBYTT _extract_transferfile printer_thumbnail_filename is %s.", printer_thumbnail_filename)
            self._write_lines_to_text_file(printer_thumbnail_filename, matches_e3s1proforkbytt_final, gcode_filename)

    def _write_lines_to_text_file(self, printer_thumbnail_filename, lines, gcode_filename):
        # Write only M4010 lines (each line)
        with open(printer_thumbnail_filename, 'w', encoding='utf-8') as f:
            for line_tuple in lines:
                blob = ''.join(line_tuple)
                for raw in blob.splitlines():
                    if re.match(r'^\s*M4010\b', raw):
                        f.write(raw.rstrip() + '\n')
        self._logger.debug("E3S1PROFORKBYTT _write_lines_to_text_file written to %s.", printer_thumbnail_filename)
        self._logger.debug("E3S1PROFORKBYTT _write_lines_to_text_file gcode_filename is %s.", gcode_filename)

        # enforce only M4010
        self._filter_helper_file(printer_thumbnail_filename)

        self.prepare_file(printer_thumbnail_filename, gcode_filename)

    # Qidi decode
    def _extract_e3s1proforkbytt_thumbnail(self, match):
        encoded_jpg = base64.b64decode(match.replace("; ", "").encode())
        with io.BytesIO(encoded_jpg) as jpg_bytes:
            image = Image.open(jpg_bytes)
            return self._imageToJpg(image)

    def _imageToJpg(self, image):
        with io.BytesIO() as jpg_bytes:
            image.save(jpg_bytes, "JPEG")
            jpg_bytes_string = jpg_bytes.getvalue()
        return jpg_bytes_string

    # ~~ EventHandlerPlugin mixin

    def on_event(self, event, payload):
        self._logger.debug("event all is %s", str(event))
        self._logger.debug("API Key 'E3S1PROFORKBYTT_Thumbnails': %s", self.api_key)
        self._logger.debug("self.selectedPrintFilename on event is %s", str(self.selectedPrintFilename))
        self._logger.debug("self.octodgusFilename on event is %s", str(self.octodgusFilename))

        # inside on_event
        if event == "SettingsUpdated":
            self.api_key = self._settings.get(["api_key"])
            self._logger.debug("SettingsUpdated -> refreshed API key.")

        # Ignore FileSelected for helper (any case)
        if event == "FileSelected":
            self._logger.debug("payload[name] on event is %s", str(payload.get("name")))
            if payload.get("name", "").upper() == HELPER_BASENAME:
                self._logger.debug("Ignoring FileSelected for helper %s", payload.get("name"))
                return

        # Safety: if analysis tries to start on the staged helper, purge it and bail
        if event == "MetadataAnalysisStarted" and payload.get("name", "").upper() == HELPER_BASENAME:
            self._logger.debug("Analysis started for helper; purging uploads copy.")
            self._purge_uploads_helper(payload.get("path"))
            return

        if event == "PrintStarted" and self.selectedPrintFilename != "None":
            self._logger.debug("event PrintStarted")
            octodgusStarted = f"M19 S3 ; Update LCD"
            self._printer.commands(octodgusStarted)
            self._logger.debug("M19 S3 sent to LCD Display: %s", str(octodgusStarted))
            display_name = os.path.splitext(os.path.basename(self.selectedPrintFilename))[0]
            fileStartedM117 = f"M117 {display_name} ; Update LCD"
            self._printer.commands(fileStartedM117)
            self._logger.debug("Sending M117 display_name on Printstart to LCD display: %s", str(fileStartedM117))
            self._logger.debug("self.selectedPrintFilename set to: %s", str(self.selectedPrintFilename))

        if event == "PrintResumed" and self.selectedPrintFilename != "None":
            self._logger.debug("event PrintResumed")
            self._printer.commands("M19 S5 ; Update LCD")

        if event == "PrintCancelled" and self.selectedPrintFilename != "None":
            self._logger.debug("event PrintCancelled")
            self._printer.commands("M19 S2 ; Update LCD")
            self.selectedPrintFilename = "None"

        if event == "PrintPaused" and self.selectedPrintFilename != "None":
            self._logger.debug("event PrintPaused")
            self._printer.commands("M19 S4 ; Update LCD")

        if event == "PrintDone" and self.selectedPrintFilename != "None":
            self._logger.debug("event PrintDone")
            self._printer.commands("M19 S6 ; Update LCD")
            self.selectedPrintFilename = "None"

        # === TransferDone: only act when our helper just finished ===
        if event == "TransferDone" and payload.get("local", "").upper() == HELPER_BASENAME:
            self._logger.debug("TransferDone for helper.")
            self._logger.debug("self.selectedPrintFilename on TransferDone is %s", str(self.selectedPrintFilename))
            self._logger.debug("self.selectedPrintFolderRel on TransferDone is %s", str(self.selectedPrintFolderRel))
            self._logger.debug("self.octodgusFilename on event is %s", str(self.octodgusFilename))

            # Now it's safe: purge the staged helper from uploads
            self._purge_uploads_helper(payload.get("local"))

            # Clean temporary helper file in plugin data
            plugin_helper_path = os.path.join(self.get_plugin_data_folder(), HELPER_BASENAME)
            if os.path.exists(plugin_helper_path):
                try:
                    os.remove(plugin_helper_path)
                except Exception as e:
                    self._logger.debug("Could not remove helper in plugin data: %s", e)

            # Build RELATIVE path for the originally selected file
            file_location = "local"
            rel_path = os.path.join(self.selectedPrintFolderRel or "", self.selectedPrintFilename)
            self._logger.debug("rel_path on TransferDone is %s", str(rel_path))
            path_select_file = self._file_manager.path_on_disk(file_location, rel_path)
            self._logger.debug("path_select_file on TransferDone is %s", str(path_select_file))

            # Suppress the *next* FileSelected of the original file (caused by our own select)
            self._suppress_next_file_selected_name = self.selectedPrintFilename

            self._printer.select_file(path_select_file, False, False)

            self._printer.commands("M19 S1 ; Update LCD")
            display_name = os.path.splitext(os.path.basename(self.selectedPrintFilename))[0]
            self._printer.commands(f"M117 {display_name} ; Update LCD")

            # Reset flags
            self.octodgusFilename = "None"
            self._active_helper_inflight = False
            self._logger.debug("self.octodgusFilename set to: %s", str(self.octodgusFilename))

        # Early exit for non-file events handled above
        if event not in ["FileAdded", "FileRemoved", "FolderRemoved", "FolderAdded", "FileSelected", "PrintStarted"]:
            return

        if event == "FolderRemoved" and payload.get("storage") == "local":
            import shutil
            shutil.rmtree(self.get_plugin_data_folder() + "/" + payload.get("path", ""), ignore_errors=True)

        if event == "FolderAdded" and payload.get("storage") == "local":
            file_list = self._file_manager.list_files(path=payload["path"], recursive=True)
            local_files = file_list["local"]
            results = dict(no_thumbnail=[], no_thumbnail_src=[])
            for file_key, file in local_files.items():
                results = self._process_gcode(local_files[file_key], results)
            self._logger.debug("Scan results: {}".format(results))

        # NOTE: Do NOT purge the helper on FileAdded — that can kill an SD upload in progress.

        if event in ["FileAdded", "FileRemoved"] and payload.get("storage") == "local" and payload.get("name", False):
            # Ignore helper during our own staging in uploads
            if payload.get("name", "").upper() == HELPER_BASENAME:
                self._logger.debug("event %s: helper %s found in uploads. Aborting handling.", event, payload.get("name"))
                return

            file_extension = os.path.splitext(payload["name"])[1].lower()
            self._logger.debug("event FileAdded/FileRemoved first")
            if file_extension != ".gcode":
                return  # Skip non-gcode files

            thumbnail_name_jpg = self.regex_extension.sub(".jpg", payload["name"])
            thumbnail_path_jpg = self.regex_extension.sub(".jpg", payload["path"])
            regex_e3s1proforkbytt_content = r"(?:^; (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)([\s\S]*?)(?:^; (?:thumbnail(?:_JPG)*|jpg) end|\Z)"
            gcode_filename = self._file_manager.path_on_disk("local", payload["path"])
            with open(gcode_filename, "rb") as gcode_file:
                gcode_content = gcode_file.read().decode("utf-8", "ignore")
                self.use_e3s1proforkbytt = bool(re.search(regex_e3s1proforkbytt_content, gcode_content, re.MULTILINE))

            if not self._settings.get_boolean(["use_uploads_folder"]):
                thumbnail_filename = "{}/{}".format(self.get_plugin_data_folder(), thumbnail_path_jpg)
            else:
                thumbnail_filename = self._file_manager.path_on_disk("local", thumbnail_path_jpg)

            if os.path.exists(thumbnail_filename):
                os.remove(thumbnail_filename)

            if event == "FileAdded":
                self._logger.debug("event FileAdded inside")
                self._extract_thumbnail(gcode_filename, thumbnail_filename)
                if os.path.exists(thumbnail_filename):
                    thumbnail_url = "plugin/e3s1proforkbyttthumbnails/thumbnail/{}?{:%Y%m%d%H%M%S}".format(
                        thumbnail_path_jpg.replace(thumbnail_name_jpg, quote(thumbnail_name_jpg)), datetime.datetime.now())
                    self._file_manager.set_additional_metadata("local", payload["path"], "thumbnail",
                                                               thumbnail_url.replace("//", "/"), overwrite=True)
                    self._file_manager.set_additional_metadata("local", payload["path"], "thumbnail_src",
                                                               self._identifier, overwrite=True)

        # --- FileSelected handler with loop prevention ---
        if event == "FileSelected":
            name_upper = payload.get("name", "").upper()

            # Ignore the helper if anyone selects it
            if name_upper == HELPER_BASENAME:
                self._logger.debug("Ignoring FileSelected for helper %s (main block)", payload.get("name"))
                return

            # Suppress our own reselect
            if self._suppress_next_file_selected_name and payload.get("name") == self._suppress_next_file_selected_name:
                self._logger.debug("Suppressing self-triggered FileSelected for %s", payload.get("name"))
                self._suppress_next_file_selected_name = None
                return

            # If helper upload in flight, ignore further selections to avoid loops
            if self._active_helper_inflight:
                self._logger.debug("Helper upload inflight; ignoring FileSelected for %s", payload.get("name"))
                return

            # Normal path: generate+upload helper for the selected file
            file_extension = os.path.splitext(payload["name"])[1].lower()
            if file_extension != ".gcode":
                return  # Skip non-gcode files

            thumbnail_name_jpg = self.regex_extension.sub(".jpg", payload["name"])
            thumbnail_path_jpg = self.regex_extension.sub(".jpg", payload["path"])
            regex_e3s1proforkbytt_content = r"(?:^; (?:thumbnail(?:_JPG)*|jpg) begin 250x250 \d+ 1 \d+(?:\s+\d+)*.*?$)([\s\S]*?)(?:^; (?:thumbnail(?:_JPG)*|jpg) end|\Z)"
            gcode_filename = self._file_manager.path_on_disk("local", payload["path"])
            with open(gcode_filename, "rb") as gcode_file:
                gcode_content = gcode_file.read().decode("utf-8", "ignore")
                self.use_e3s1proforkbytt = bool(re.search(regex_e3s1proforkbytt_content, gcode_content, re.MULTILINE))

            if not self._settings.get_boolean(["use_uploads_folder"]):
                thumbnail_filename = "{}/{}".format(self.get_plugin_data_folder(), thumbnail_path_jpg)
            else:
                thumbnail_filename = self._file_manager.path_on_disk("local", thumbnail_path_jpg)
            if os.path.exists(thumbnail_filename):
                os.remove(thumbnail_filename)

            self._extract_thumbnail(gcode_filename, thumbnail_filename)
            if os.path.exists(thumbnail_filename):
                thumbnail_url = "plugin/e3s1proforkbyttthumbnails/thumbnail/{}?{:%Y%m%d%H%M%S}".format(
                    thumbnail_path_jpg.replace(thumbnail_name_jpg, quote(thumbnail_name_jpg)), datetime.datetime.now())
                self._file_manager.set_additional_metadata("local", payload["path"], "thumbnail",
                                                           thumbnail_url.replace("//", "/"), overwrite=True)
                self._file_manager.set_additional_metadata("local", payload["path"], "thumbnail_src",
                                                           self._identifier, overwrite=True)

            self._logger.debug("payload name: %s", payload["name"])

            # Helper file path ONLY in plugin data (never in uploads)
            printer_thumbnail_filename = os.path.join(self.get_plugin_data_folder(), HELPER_BASENAME)

            # Save RELATIVE details of the user-selected file (from uploads root)
            self.selectedPrintFilename = payload["name"]
            self.selectedPrintFolderRel = os.path.dirname(payload["path"])  # "" if at root

            # Keep absolute path marker for helper
            self.octodgusFilename = printer_thumbnail_filename

            self._logger.debug("self.octodgusFilename on event FileSelected is %s", str(self.octodgusFilename))
            self._logger.debug("self.selectedPrintFilename on event FileSelected is %s", str(self.selectedPrintFilename))
            self._logger.debug("self.selectedPrintFolderRel on event FileSelected is %s", str(self.selectedPrintFolderRel))
            self._logger.debug("printer_thumbnail_filename is %s", printer_thumbnail_filename)
            self._logger.debug("gcode_filename is %s", str(gcode_filename))

            # Build the helper content and upload it (sets _active_helper_inflight = True in prepare_file)
            self._extract_transferfile(gcode_filename, printer_thumbnail_filename)

    # ~~ SimpleApiPlugin mixin

    def _process_gcode(self, gcode_file, results=None):
        if results is None:
            results = []
        self._logger.debug(gcode_file["path"])
        if gcode_file.get("type") == "machinecode":
            self._logger.debug(gcode_file.get("thumbnail"))
            if gcode_file.get("thumbnail") is None or not os.path.exists("{}/{}".format(self.get_plugin_data_folder(), self.regex_extension.sub(".png", gcode_file["path"]))):
                self._logger.debug("No Thumbnail for %s, attempting extraction" % gcode_file["path"])
                results["no_thumbnail"].append(gcode_file["path"])
                self.on_event("FileAdded", {'path': gcode_file["path"], 'storage': "local", 'type': ["gcode"],
                                            'name': gcode_file["name"]})
            elif "e3s1proforkbyttthumbnails" in gcode_file.get("thumbnail") and not gcode_file.get("thumbnail_src"):
                self._logger.debug("No Thumbnail source for %s, adding" % gcode_file["path"])
                results["no_thumbnail_src"].append(gcode_file["path"])
                self._file_manager.set_additional_metadata("local", gcode_file["path"], "thumbnail_src",
                                                           self._identifier, overwrite=True)
        elif gcode_file.get("type") == "folder" and gcode_file.get("children") is not None:
            children = gcode_file["children"]
            for key, file in children.items():
                self._process_gcode(children[key], results)
        return results

    def on_after_startup(self):
        # Access the API key from the settings
        self.api_key = self._settings.get(["api_key"])

    def get_api_commands(self):
        return dict(crawl_files=[])

    def on_api_command(self, command, data):
        import flask
        if not Permissions.PLUGIN_E3S1PROFORKBYTTTHUMBNAILS_SCAN.can():
            return flask.make_response("Insufficient rights", 403)

        if command == "crawl_files":
            return flask.jsonify(self.scan_files())

    def scan_files(self):
        self._logger.debug("Crawling Files")
        file_list = self._file_manager.list_files(recursive=True)
        self._logger.debug(file_list)
        local_files = file_list["local"]
        results = dict(no_thumbnail=[], no_thumbnail_src=[])
        for key, f in local_files.items():
            # Skip helper if it ever appears
            if f.get("name", "").upper() == HELPER_BASENAME:
                continue
            results = self._process_gcode(local_files[key], results)
        self.file_scanner = None
        return results

    # ~~ extension_tree hook
    def get_extension_tree(self, *args, **kwargs):
        return dict(
            machinecode=dict(
                gcode=["txt"]
            )
        )

    # ~~ Routes hook
    def route_hook(self, server_routes, *args, **kwargs):
        from octoprint.server.util.tornado import LargeResponseHandler, path_validation_factory
        from octoprint.util import is_hidden_path
        thumbnail_root_path = self._file_manager.path_on_disk("local", "") if self._settings.get_boolean(["use_uploads_folder"]) else self.get_plugin_data_folder()
        return [
            (r"thumbnail/(.*)", LargeResponseHandler,
             {'path': thumbnail_root_path, 'as_attachment': False, 'path_validation': path_validation_factory(
                 lambda path: not is_hidden_path(path), status_code=404)})
        ]

    # ~~ Server API Before Request Hook

    def hook_octoprint_server_api_before_request(self, *args, **kwargs):
        return [self.update_file_list]

    def update_file_list(self):
        if self._settings.get_boolean(["sync_on_refresh"]) and flask.request.path.startswith('/api/files') and flask.request.method == 'GET' and not self.file_scanner:
            from threading import Thread
            self.file_scanner = Thread(target=self.scan_files, daemon=True)
            self.file_scanner.start()

    # ~~ Access Permissions Hook

    def get_additional_permissions(self, *args, **kwargs):
        return [
            {'key': "SCAN", 'name': "Scan Files", 'description': gettext("Allows access to scan files."),
             'roles': ["admin"], 'dangerous': True, 'default_groups': [ADMIN_GROUP]}
        ]

    # ~~ Softwareupdate hook

    def get_update_information(self):
        return {'e3s1proforkbyttthumbnails': {'displayName': "E3S1PROFORKBYTT Thumbnails", 'displayVersion': self._plugin_version,
                                              'type': "github_release", 'user': "jneilliii",
                                              'repo': "OctoPrint-PrusaSlicerThumbnails", 'current': self._plugin_version,
                                              'stable_branch': {'name': "Stable", 'branch': "master",
                                                                'comittish': ["master"]}, 'prerelease_branches': [
                                                  {'name': "Release Candidate", 'branch': "rc", 'comittish': ["rc", "master"]}
                                              ],
                                              'pip': "https://github.com/ThomasToka/OctoPrint-PrusaSlicerThumbnails/archive/refs/heads/E3S1PROFORKBYTT.zip"}}


    # ~~ Backup hook

    def additional_backup_excludes(self, excludes, *args, **kwargs):
        if "uploads" in excludes:
            return ["."]
        return []


__plugin_name__ = "E3S1PROFORKBYTTT Thumbnails"
__plugin_pythoncompat__ = ">=2.7,<4"  # python 2 and 3


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = E3S1PROFORKBYTTThumbnailsPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.filemanager.extension_tree": __plugin_implementation__.get_extension_tree,
        "octoprint.server.http.routes": __plugin_implementation__.route_hook,
        "octoprint.server.api.before_request": __plugin_implementation__.hook_octoprint_server_api_before_request,
        "octoprint.access.permissions": __plugin_implementation__.get_additional_permissions,
        "octoprint.plugin.backup.additional_excludes": __plugin_implementation__.additional_backup_excludes,
    }
