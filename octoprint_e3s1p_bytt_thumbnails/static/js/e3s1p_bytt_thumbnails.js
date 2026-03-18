/*
 * View model for E3S1P ByTT Thumbnails
 *
 * Author: jneilliii
 * License: AGPLv3
 */
$(function() {
    function E3s1pByttThumbnailsViewModel(parameters) {
        var self = this;
        var pluginKey = "e3s1p_bytt_thumbnails";

		self.settingsViewModel = parameters[0];
		self.filesViewModel = parameters[1];
		self.printerStateViewModel = parameters[2];
		self.uploadmanagerViewModel = parameters[3];

		self.thumbnail_url = ko.observable('/static/img/tentacle-20x20.png');
		self.thumbnail_title = ko.observable('');
		self.inline_thumbnail = ko.observable();
		self.file_details = ko.observable();
		self.crawling_files = ko.observable(false);
		self.crawl_results = ko.observableArray([]);
		self._refreshTimer = undefined;

        self.DEFAULT_THUMBNAIL_SCALE = "100%";
        self.DEFAULT_THUMBNAIL_ALIGN = "left";
        self.DEFAULT_THUMBNAIL_POSITION = false;

        self.filesViewModel.thumbnailScaleValue = ko.observable(self.DEFAULT_THUMBNAIL_SCALE);
        self.filesViewModel.thumbnailAlignValue = ko.observable(self.DEFAULT_THUMBNAIL_ALIGN);
        self.filesViewModel.thumbnailPositionLeft = ko.observable(self.DEFAULT_THUMBNAIL_POSITION);

        self.isPluginThumbnail = function(data) {
            return data && data.thumbnail_src === pluginKey;
        };

        self.getPluginSettings = function() {
            if (!self.settingsViewModel || !self.settingsViewModel.settings || !self.settingsViewModel.settings.plugins) {
                return null;
            }

            return self.settingsViewModel.settings.plugins[pluginKey];
        };

        self.openThumbnail = function(data) {
            if (!self.isPluginThumbnail(data)) {
                return;
            }

            self.thumbnail_url(data.thumbnail);
            self.thumbnail_title(data.name.replace(/\.(?:gco(?:de)?|tft)$/,''));
            self.file_details(data);
            $("div#prusa_thumbnail_viewer").modal("show");
        };

		self.filesViewModel.e3s1p_bytt_thumbnails_open_thumbnail = self.openThumbnail;

		self.crawl_files = function(){
			self.crawling_files(true);
			self.crawl_results([]);
			$.ajax({
				url: API_BASEURL + "plugin/e3s1p_bytt_thumbnails",
				type: "POST",
				dataType: "json",
				data: JSON.stringify({
					command: "crawl_files"
				}),
				contentType: "application/json; charset=UTF-8"
			}).done(function(data){
				for (key in data) {
					if(data[key].length){
						self.crawl_results.push({name: ko.observable(key), files: ko.observableArray(data[key])});
					}
				}
				if(self.crawl_results().length === 0){
					self.crawl_results.push({name: ko.observable('No convertible files found'), files: ko.observableArray([])});
				}
				self.filesViewModel.requestData({force: true});
				self.crawling_files(false);
			}).fail(function(data){
				self.crawling_files(false);
			});
		};

		self.requestFilesRefresh = function() {
			if (!self.filesViewModel || !self.filesViewModel.requestData) {
				return;
			}

			if (self._refreshTimer) {
				clearTimeout(self._refreshTimer);
			}

			self._refreshTimer = setTimeout(function() {
				self.filesViewModel.requestData({force: true});
				self._refreshTimer = undefined;
			}, 250);
		};

		self.onEventFileAdded = function(payload) {
			self.requestFilesRefresh();
		};

        self.applyInitialSettings = function() {
            var pluginSettings = self.getPluginSettings();
            if (!pluginSettings) {
                return;
            }

            if (pluginSettings.scale_inline_thumbnail() === true) {
                self.filesViewModel.thumbnailScaleValue(pluginSettings.inline_thumbnail_scale_value() + "%");
            }

            if (pluginSettings.align_inline_thumbnail() === true) {
                self.filesViewModel.thumbnailAlignValue(pluginSettings.inline_thumbnail_align_value());
            }

            if (pluginSettings.resize_filelist()) {
                $("#files > div > div.gcode_files > div.scroll-wrapper").css({
                    "height": pluginSettings.filelist_height() + "px"
                });
            }

            if (pluginSettings.inline_thumbnail_position_left() === true) {
                self.filesViewModel.thumbnailPositionLeft(true);
            }
        };

        self.bindSettingObservers = function() {
            var pluginSettings = self.getPluginSettings();
            if (!pluginSettings) {
                return;
            }

            pluginSettings.scale_inline_thumbnail.subscribe(function(newValue){
                if (newValue === false) {
                    self.filesViewModel.thumbnailScaleValue(self.DEFAULT_THUMBNAIL_SCALE);
                } else {
                    self.filesViewModel.thumbnailScaleValue(pluginSettings.inline_thumbnail_scale_value() + "%");
                }
            });

            pluginSettings.inline_thumbnail_scale_value.subscribe(function(newValue){
                self.filesViewModel.thumbnailScaleValue(newValue + "%");
            });

            pluginSettings.state_panel_thumbnail_scale_value.subscribe(function() {
                $("#prusaslicer_state_thumbnail").attr({
                    "width": pluginSettings.state_panel_thumbnail_scale_value() + "%"
                });
                if (pluginSettings.state_panel_thumbnail_scale_value() !== 100) {
                    $("#prusaslicer_state_thumbnail").addClass("pull-left").next("hr").remove();
                }
            });

            pluginSettings.align_inline_thumbnail.subscribe(function(newValue){
                if (newValue === false) {
                    self.filesViewModel.thumbnailAlignValue(self.DEFAULT_THUMBNAIL_ALIGN);
                } else {
                    self.filesViewModel.thumbnailAlignValue(pluginSettings.inline_thumbnail_align_value());
                }
            });

            pluginSettings.inline_thumbnail_align_value.subscribe(function(newValue){
                self.filesViewModel.thumbnailAlignValue(newValue);
            });

            pluginSettings.inline_thumbnail_position_left.subscribe(function(newValue){
                self.filesViewModel.thumbnailPositionLeft(newValue);
            });

            pluginSettings.filelist_height.subscribe(function(newValue){
                if (pluginSettings.resize_filelist()) {
                    $("#files > div > div.gcode_files > div.scroll-wrapper").css({
                        "height": newValue + "px"
                    });
                }
            });
        };

        self.bindStatePanelThumbnail = function() {
            self.printerStateViewModel.dateString.subscribe(function(data){
                var pluginSettings = self.getPluginSettings();
                if (!pluginSettings) {
                    return;
                }

                if(data && data !== "unknown"){
                    OctoPrint.files.get("local", self.printerStateViewModel.filepath())
                        .done(function(file_data){
                            if(file_data){
                                if(pluginSettings.state_panel_thumbnail() && file_data.thumbnail && file_data.thumbnail_src === pluginKey){
                                    if($("#prusaslicer_state_thumbnail").length) {
                                        $("#prusaslicer_state_thumbnail").attr("src", file_data.thumbnail);
                                    } else {
                                        $("#state > div > hr:first").after('<img id="prusaslicer_state_thumbnail" class="pull-left" src="'+file_data.thumbnail+'" width="' + pluginSettings.state_panel_thumbnail_scale_value() + '%"/>');
                                        if(pluginSettings.state_panel_thumbnail_scale_value() === 100) {
                                            $("#prusaslicer_state_thumbnail").removeClass("pull-left").after('<hr id="prusaslicer_state_hr">');
                                        }
                                        if(pluginSettings.relocate_progress()) {
                                            $("#state > div > div.progress.progress-text-centered").css({"margin-bottom": "inherit"}).insertBefore("#prusaslicer_state_thumbnail").after("<hr>");
                                        }
                                    }
                                } else {
                                    $("#prusaslicer_state_thumbnail").remove();
                                }
                            }
                        })
                        .fail(function(){
                            if($("#prusaslicer_state_thumbnail").length) {
                                $("#prusaslicer_state_thumbnail").remove();
                            }
                        });
                } else {
                    $("#prusaslicer_state_thumbnail").remove();
                    if(pluginSettings.state_panel_thumbnail_scale_value() === 100) {
                        $("#prusaslicer_state_hr").remove();
                    }
                }
            });
        };

		self.onBeforeBinding = function() {
		    // inject filelist thumbnail into template

            var fileListButtonRegex = /<div class="btn-group action-buttons">([\s\S]*)<.div>/mi;
			var modalButtonTemplate = '<div class="btn btn-mini" data-bind="click: function() { if ($root.loginState.isUser()) { $root.e3s1p_bytt_thumbnails_open_thumbnail($data) } else { return; } }, visible: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settingsViewModel.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail() == false)" title="Show Thumbnail" style="display: none;"><i class="fa fa-image"></i></div>';

			var inlineThumbnailTemplate = '<div class="inline_prusa_thumbnail" ' +
			                                'data-bind="if: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settingsViewModel.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail() == true), style: {\'text-align\': $root.thumbnailAlignValue, \'width\': ($root.thumbnailPositionLeft()) ? $root.thumbnailScaleValue() : \'100%\'}, css: {\'row-fluid\': !$root.thumbnailPositionLeft(), \'pull-left\': $root.thumbnailPositionLeft()}">' +
			                                '<img data-bind="attr: {src: $data.thumbnail}, ' +
			                                'visible: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settingsViewModel.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail() == true), ' +
			                                'click: function() { if ($root.loginState.isUser() && !($(\'html\').attr(\'id\') === \'touch\')) { $root.e3s1p_bytt_thumbnails_open_thumbnail($data) } else { return; } },' +
                                            'style: {\'width\': (!$root.thumbnailPositionLeft()) ? $root.thumbnailScaleValue() : \'100%\' }" ' +
			                                'style="display: none;"/></div>';

			$("#files_template_machinecode").text(function () {
				var updatedTemplate = inlineThumbnailTemplate + $(this).text();
				updatedTemplate = updatedTemplate.replace(fileListButtonRegex, '<div class="btn-group action-buttons">$1	' + modalButtonTemplate + '></div>');
				return updatedTemplate;
			});

            // new upload manager injection if inline thumbnails is enabled.
            if(self.uploadmanagerViewModel) {
                $("#uploadmanager_template_machinecode").text(function () {
                    var uploadManagerThumbnailTemplate = '<i class="fa-regular fa-file-lines" data-bind="visible: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settings.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail_uploadmanager() == false)"></i><div class="inline_prusa_thumbnail" ' +
                                                'data-bind="if: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settings.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail_uploadmanager() == true)">' +
                                                '<img data-bind="attr: {src: $data.thumbnail}, ' +
                                                'visible: ($data.thumbnail_src == \'e3s1p_bytt_thumbnails\' && $root.settings.settings.plugins.e3s1p_bytt_thumbnails.inline_thumbnail_uploadmanager() == true), ' +
                                                'click: function() { if ($root.loginState.isUser() && !($(\'html\').attr(\'id\') === \'touch\')) { $root.files.e3s1p_bytt_thumbnails_open_thumbnail($data) } else { return; } },' +
                                                'style="display: none;"/></div>';
                    var uploadManagerIconRegex = /<i class="fa-regular fa-file-lines"><\/i>/mi;
                    var updatedUploadTemplate = $(this).text();
                    updatedUploadTemplate = updatedUploadTemplate.replace(uploadManagerIconRegex, '' + uploadManagerThumbnailTemplate);
                    return updatedUploadTemplate;
                });
            }

            self.applyInitialSettings();
            self.bindSettingObservers();
            self.bindStatePanelThumbnail();
		};

	}

	OCTOPRINT_VIEWMODELS.push({
		construct: E3s1pByttThumbnailsViewModel,
		dependencies: ['settingsViewModel', 'filesViewModel', 'printerStateViewModel', 'uploadmanagerViewModel'],
		optional: ['uploadmanagerViewModel'],
		elements: ['div#prusa_thumbnail_viewer', '#crawl_files', '#crawl_files_results']
	});
});
