import mimetypes
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from Tools.MCPServer.security import (
    MCP_AUTH_HEADER,
    MCP_CALLER_HEADER,
    MCP_CALLER_VALUE,
)
from Tools.WebServer import web_server


class PlatformThinkingApiTests(unittest.TestCase):
    def setUp(self):
        self.token_patcher = mock.patch.object(
            web_server,
            "MCP_AUTH_TOKEN",
            "unit-test-token",
        )
        self.service_patcher = mock.patch.object(
            web_server,
            "set_active_platform_thinking",
            return_value={
                "active_profile": "default",
                "platform_tag": "deepseek",
                "think_switch": False,
                "think_depth": "max",
            },
        )
        self.token_patcher.start()
        self.service = self.service_patcher.start()
        self.client = TestClient(web_server.app)
        self.mcp_headers = {
            MCP_CALLER_HEADER: MCP_CALLER_VALUE,
            MCP_AUTH_HEADER: "unit-test-token",
        }

    def tearDown(self):
        self.service_patcher.stop()
        self.token_patcher.stop()

    def test_bare_http_request_is_rejected(self):
        response = self.client.post(
            "/api/platforms/thinking",
            json={"platform": "deepseek", "think_switch": False},
        )

        self.assertEqual(response.status_code, 403)
        self.service.assert_not_called()

    def test_authenticated_mcp_request_updates_only_the_switch(self):
        response = self.client.post(
            "/api/platforms/thinking",
            headers=self.mcp_headers,
            json={"platform": "deepseek", "think_switch": False},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["platform_tag"], "deepseek")
        self.service.assert_called_once_with("deepseek", False)

    def test_integer_boolean_is_rejected(self):
        response = self.client.post(
            "/api/platforms/thinking",
            headers=self.mcp_headers,
            json={"platform": "deepseek", "think_switch": 0},
        )

        self.assertEqual(response.status_code, 422)
        self.service.assert_not_called()

    def test_extra_fields_are_rejected(self):
        response = self.client.post(
            "/api/platforms/thinking",
            headers=self.mcp_headers,
            json={
                "platform": "deepseek",
                "think_switch": False,
                "api_key": "must-not-be-accepted",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.service.assert_not_called()


class WebRuntimeContractTests(unittest.TestCase):
    def test_queue_request_accepts_only_runtime_task_types(self):
        for task_type in (1000, 2000, 4000):
            request = web_server.QueueTaskItem(
                task_type=task_type,
                input_path="task.json",
            )
            self.assertEqual(request.task_type, task_type)

        with self.assertRaises(ValidationError):
            web_server.QueueTaskItem(task_type=1, input_path="task.json")

    def test_javascript_modules_use_browser_compatible_mime_type(self):
        self.assertEqual(
            mimetypes.guess_type("module.mjs")[0],
            "application/javascript",
        )


if __name__ == "__main__":
    unittest.main()
