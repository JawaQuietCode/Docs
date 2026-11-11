import unittest

from agent.format import parse_reply, replies_to_text, to_replies


class HtmlDeliveryTests(unittest.TestCase):
    def test_html_fence_becomes_standalone_file_not_webview(self):
        source = "```html\n<!doctype html><html><body><h1>Halo</h1></body></html>\n```"
        blocks = parse_reply(source)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "html_file")
        self.assertEqual(blocks[0]["filename"], "kwee-output.html")
        self.assertIn("<h1>Halo</h1>", blocks[0]["html"])

    def test_complete_html_document_after_prose_is_separate_asset(self):
        source = "Ini file HTML-nya.\n\n<!doctype html><html><body>Isi</body></html>"
        blocks = parse_reply(source)
        self.assertEqual([b["type"] for b in blocks], ["text", "html_file"])
        self.assertEqual(blocks[0]["text"], "Ini file HTML-nya.")

    def test_wa_html_is_file_but_web2ui_is_explicit_inline_preview(self):
        html = '<!doctype html><html><body>file</body></html>'
        file_reply = to_replies('```wa:html\n{"html": "' + html.replace('"', '\\"') + '", "filename": "report.html"}\n```')
        self.assertEqual(file_reply[0]["type"], "html_file")
        self.assertEqual(file_reply[0]["filename"], "report.html")

        preview = to_replies('```wa:web2ui\n' + html + '\n```')
        self.assertEqual(preview[0]["type"], "web2ui")

    def test_html_file_is_preserved_in_history_as_document_marker(self):
        reply = {"type": "html_file", "html": "<!doctype html>", "filename": "report.html"}
        self.assertEqual(replies_to_text([reply]), "[dokumen HTML]")


if __name__ == "__main__":
    unittest.main()
