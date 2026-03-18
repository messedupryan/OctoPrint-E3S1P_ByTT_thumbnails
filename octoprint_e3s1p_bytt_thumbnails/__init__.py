# coding=utf-8
from __future__ import absolute_import

from .plugin_config import PLUGIN_NAME

__plugin_name__ = PLUGIN_NAME
__plugin_pythoncompat__ = ">=3.8,<4"


def __plugin_load__():
    from .plugin import E3s1p_bytt_thumbnailsPlugin

    global __plugin_implementation__
    __plugin_implementation__ = E3s1p_bytt_thumbnailsPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.filemanager.extension_tree": __plugin_implementation__.get_extension_tree,
        "octoprint.filemanager.preprocessor": __plugin_implementation__.hook_octoprint_filemanager_preprocessor,
        "octoprint.server.http.routes": __plugin_implementation__.route_hook,
        "octoprint.server.api.before_request": __plugin_implementation__.hook_octoprint_server_api_before_request,
        "octoprint.access.permissions": __plugin_implementation__.get_additional_permissions,
        "octoprint.plugin.backup.additional_excludes": __plugin_implementation__.additional_backup_excludes,
    }
