# coding=utf-8
"""
Plugin configuration and constants.
"""

# Plugin metadata
PLUGIN_NAME = "E3S1P ByTT Thumbnails"
PLUGIN_VERSION = "3.0.0"
PLUGIN_PYTHON_COMPAT = ">=3.8,<4"

# Helper filename
HELPER_BASENAME = "OCTODGUS.GCO"

# Default socket timeout
DEFAULT_TIMEOUT = 15

# Default settings
DEFAULT_SETTINGS = {
    "installed": True,
    "inline_thumbnail": False,
    "scale_inline_thumbnail": False,
    "inline_thumbnail_scale_value": "50",
    "inline_thumbnail_position_left": False,
    "align_inline_thumbnail": False,
    "inline_thumbnail_align_value": "left",
    "state_panel_thumbnail": True,
    "state_panel_thumbnail_scale_value": "100",
    "resize_filelist": False,
    "filelist_height": "306",
    "scale_inline_thumbnail_position": False,
    "sync_on_refresh": False,
    "use_uploads_folder": True,
    "relocate_progress": False,
    "inline_thumbnail_uploadmanager": True,
}

# API permission
PERMISSION_SCAN = "SCAN"
PERMISSION_DESCRIPTION = "Allows access to scan files."
