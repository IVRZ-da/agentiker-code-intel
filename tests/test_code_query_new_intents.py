"""Quick verify code_query intents for new tools."""
import json
from code_intel.code_intel import code_query_tool, _QUERY_INTENT_MAP


class TestCodeQueryNewIntents:
    def test_replace_body_intent(self):
        result = code_query_tool(intent="replace_body")
        data = json.loads(result)
        assert data["routed_to"] == "code_replace_body"

    def test_safe_delete_intent(self):
        result = code_query_tool(intent="safe_delete")
        data = json.loads(result)
        assert data["routed_to"] == "code_safe_delete"

    def test_insert_before_intent(self):
        result = code_query_tool(intent="insert_before")
        data = json.loads(result)
        assert data["routed_to"] == "code_insert_before"

    def test_insert_after_intent(self):
        result = code_query_tool(intent="insert_after")
        data = json.loads(result)
        assert data["routed_to"] == "code_insert_after"

    def test_file_overview_intent(self):
        result = code_query_tool(intent="file_overview")
        data = json.loads(result)
        assert data["routed_to"] == "code_overview"

    def test_intent_map_has_new_entries(self):
        assert "replace_body" in _QUERY_INTENT_MAP
        assert "safe_delete" in _QUERY_INTENT_MAP
        assert "insert_before" in _QUERY_INTENT_MAP
        assert "insert_after" in _QUERY_INTENT_MAP
        assert "file_overview" in _QUERY_INTENT_MAP
