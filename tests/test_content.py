from __future__ import annotations

from types import SimpleNamespace
import unittest

from spamdet.content import extract_message_content


class MessageContentExtractionTests(unittest.TestCase):
    def test_text_and_caption_keep_storage_contract(self) -> None:
        text_message = SimpleNamespace(
            text="普通正文",
            caption=None,
            entities=[SimpleNamespace(type="bold", offset=0, length=4)],
        )
        caption_message = SimpleNamespace(
            text=None,
            caption="媒体 caption",
            caption_entities=[SimpleNamespace(type="italic", offset=3, length=7)],
        )

        text = extract_message_content(text_message)
        caption = extract_message_content(caption_message)

        self.assertEqual((text.text, text.caption, text.search_text, text.source),
                         ("普通正文", None, "普通正文", "text"))
        self.assertEqual((text.entities[0].type, text.entities[0].offset), ("bold", 0))
        self.assertEqual((caption.text, caption.caption, caption.search_text, caption.source),
                         ("", "媒体 caption", "媒体 caption", "caption"))
        self.assertEqual((caption.entities[0].type, caption.entities[0].offset), ("italic", 3))

    def test_checklist_is_flattened_with_utf16_offsets(self) -> None:
        message = SimpleNamespace(
            text=None,
            caption=None,
            checklist=SimpleNamespace(
                title="标题😀",
                entities=[SimpleNamespace(type="bold", offset=0, length=4)],
                tasks=[
                    SimpleNamespace(
                        text="第一项",
                        entities=[SimpleNamespace(type="italic", offset=0, length=3)],
                    )
                ],
            ),
        )

        content = extract_message_content(message)

        self.assertEqual(content.text, "标题😀\n第一项")
        self.assertEqual(content.search_text, content.text)
        self.assertEqual(content.source, "checklist")
        self.assertEqual([entity.offset for entity in content.entities], [0, 5])

    def test_binary_only_message_is_empty_without_placeholder(self) -> None:
        content = extract_message_content(
            SimpleNamespace(text=None, caption=None, photo=SimpleNamespace(file_id="secret"))
        )

        self.assertEqual((content.text, content.caption, content.search_text, content.source),
                         ("", None, "", "empty"))


if __name__ == "__main__":
    unittest.main()
