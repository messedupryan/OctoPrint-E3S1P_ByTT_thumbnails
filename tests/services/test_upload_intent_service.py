from octoprint_e3s1p_bytt_thumbnails.services.upload_intent_service import (
    UploadIntentService,
)


def test_wants_immediate_select_or_print_supports_snake_case_strings():
    payload = {"effective_print": "true"}

    assert UploadIntentService.wants_immediate_select_or_print(payload) is True
    assert UploadIntentService.wants_immediate_print(payload) is True


def test_wants_immediate_select_or_print_supports_camel_case_bools():
    payload = {"effectiveSelect": True, "effectivePrint": False}

    assert UploadIntentService.wants_immediate_select_or_print(payload) is True
    assert UploadIntentService.wants_immediate_print(payload) is False


def test_wants_immediate_select_or_print_ignores_falsey_values():
    payload = {"select": "false", "print": 0}

    assert UploadIntentService.wants_immediate_select_or_print(payload) is False
    assert UploadIntentService.wants_immediate_print(payload) is False
