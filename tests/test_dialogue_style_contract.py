"""Offline contract/transport checks; these do not score a live model's prose."""
import unittest
from unittest.mock import patch, Mock

from telegram_monitor import dialogue_style, gemini, monitor, persona


class DialogueStyleTest(unittest.TestCase):
    def test_all_specialized_voices_end_with_same_contract(self):
        for hint in (persona.AC_HINT, persona.SELF_PRESENCE_HINT,
                     persona.MECHANISM_HINT, persona.PHENOMENAL_HINT,
                     persona.MOOD_COORD_HINT):
            system = dialogue_style.finalize(persona.SOCRATIC_SYSTEM + '\n' + hint)
            self.assertTrue(system.endswith(dialogue_style.POLICY))
            self.assertEqual(dialogue_style.finalize(system), system)

    def test_structured_classifiers_untouched(self):
        for system in ('Return JSON only', '', None):
            self.assertEqual(dialogue_style.finalize(system), system)

    @patch('telegram_monitor.gemini.requests.post')
    def test_real_request_boundary_chat_tools_and_generate(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': '變動不大。'}]}}]
        }
        contents = [{'role': 'user', 'parts': [{'text': '現在呢？'}]}]
        system = persona.SOCRATIC_SYSTEM + '\n' + persona.AC_HINT
        gemini.generate_chat('fake', 'fake', system, contents)
        gemini.generate_with_tools('fake', 'fake', system, contents, [])
        gemini.generate('fake', 'fake', system, '現在呢？')
        for call in post.call_args_list:
            body = call.kwargs['json']
            self.assertTrue(body['system_instruction']['parts'][0]['text'].endswith(dialogue_style.POLICY))
            self.assertEqual(body['contents'], contents)

    def test_snapshot_short_without_losing_pair_or_time(self):
        for v, a in ((.35, .32), (.1, .1), (-.2, -.4)):
            text = monitor._coord_contract_text({'at': '11:38:16', 'v': v, 'a': a})
            self.assertIn('11:38:16', text)
            self.assertIn(f'V {v:+.2f}、A {a:+.2f}', text)
            self.assertLess(len(text), 90)
            self.assertNotIn('整份感受', text)

    def test_meaning_boundaries_preserve_all_text(self):
        text = '變動不大。\n\n前後數值可以比較，但這次沒有原因紀錄，所以我還不能確定是什麼造成的。'
        parts = monitor.bubble_split(text, paced=True)
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(parts), text.replace('\n', ''))
        self.assertNotEqual(len(parts[0]), len(parts[-1]))


if __name__ == '__main__':
    unittest.main()
