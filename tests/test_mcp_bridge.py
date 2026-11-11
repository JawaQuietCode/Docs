import tempfile
import unittest
from pathlib import Path
from integrations.mcp_bridge import MCPBridge, is_read_only_tool, validate_server_config


class MCPBridgePolicyTests(unittest.TestCase):
    def test_read_only_allowlist_rejects_mutating_and_unknown_tools(self):
        allowed = {"get_file_contents", "search_repositories", "issue_read"}
        self.assertTrue(is_read_only_tool("get_file_contents", allowed))
        self.assertFalse(is_read_only_tool("create_issue", allowed))
        self.assertFalse(is_read_only_tool("mystery_tool", allowed))
        self.assertFalse(is_read_only_tool("delete_repository", allowed))

    def test_server_config_rejects_unapproved_commands_and_missing_tool_allowlist(self):
        with self.assertRaises(ValueError):
            validate_server_config({"command": "bash", "args": ["-c", "echo unsafe"], "allow_tools": ["query"]})
        with self.assertRaises(ValueError):
            validate_server_config({"command": "docker", "args": [], "allow_tools": []})

    def test_server_config_accepts_scoped_stdio_configuration(self):
        cfg = validate_server_config({
            "enabled": True,
            "transport": "stdio",
            "command": "docker",
            "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
            "allow_tools": ["get_file_contents"],
            "read_only": True,
        })
        self.assertEqual(cfg["command"], "docker")
        self.assertTrue(cfg["read_only"])

    def test_server_status_reports_missing_graph_before_attempting_stdio_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = MCPBridge()
            status = bridge.server_status("graphify", {
                "enabled": True,
                "transport": "stdio",
                "command": "python3",
                "args": ["-m", "graphify.serve", "graphify-out/graph.json"],
                "required_files": ["graphify-out/graph.json"],
                "allow_tools": ["query_graph"],
                "read_only": True,
            }, {"root": temp})
            self.assertEqual(status["status"], "missing_files")
            self.assertFalse(status["available"])


if __name__ == "__main__":
    unittest.main()
