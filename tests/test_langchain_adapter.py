import unittest

from integrations.langchain_adapter import status, to_langchain_tools


class LangChainAdapterTests(unittest.TestCase):
    def test_status_is_explicit_when_optional_framework_is_not_installed(self):
        info = status()
        self.assertIn("available", info)
        self.assertIn("package", info)

    def test_adapter_does_not_execute_tools_during_conversion(self):
        calls = []
        registry = [{
            "name": "safe_echo",
            "schema": {"name": "safe_echo", "description": "Echo a value", "parameters": {
                "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]
            }},
            "handler": lambda args, config=None: calls.append(args) or {"value": args["value"]},
        }]
        result = to_langchain_tools(registry)
        self.assertEqual(calls, [])
        self.assertIsInstance(result, list)
        if result:
            self.assertEqual(result[0].name, "safe_echo")


if __name__ == "__main__":
    unittest.main()
