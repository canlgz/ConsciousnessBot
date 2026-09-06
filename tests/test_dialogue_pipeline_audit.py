"""跨來源／日期／生成輸入／送達／插話的離線回歸。不得連接真實 Telegram 或模型。"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, coach, config, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo('Asia/Taipei')
NOW = datetime(2026, 9, 5, 1, 55, tzinfo=timezone.utc)
QUOTE = '剛剛讀完了日常進度，第7，8品達成'


class DialoguePipelineAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(str(Path(self.temp.name) / 'state.json'))
        self.saved_turn = dict(monitor._TURN)
        monitor._TURN.clear()
        self.addCleanup(self.restore)

    def restore(self):
        monitor._TURN.clear()
        monitor._TURN.update(self.saved_turn)

    def snapshot(self, records):
        return analyzer.analyze({'records': records, 'contexts': [], 'journeys': [], 'meta': {}}, NOW, TZ)

    def test_timestamp_formats_agree_through_analyzer_and_dialogue(self):
        instant = datetime(2026, 9, 5, 1, 24, tzinfo=timezone.utc)
        for ts in (instant, instant.isoformat(), instant.timestamp(), instant.timestamp() * 1000):
            records = [{'ts': ts, 'text': QUOTE}]
            snap = self.snapshot(records)
            ground = monitor._write_ground_data(snap, {'records': records}, NOW.timestamp(), TZ)
            self.assertEqual(snap.summary['today'], 1, repr(ts))
            self.assertEqual(ground['today_count'], 1, repr(ts))
            self.assertEqual(ground['last_label'], '今天 09:24')

    def test_future_record_cannot_be_last_completed_write(self):
        records = [{'ts': '2026-09-04T01:24:00Z'}, {'ts': '2026-09-05T23:24:00Z'}]
        snap = self.snapshot(records)
        self.assertEqual(snap.summary['today'], 0)
        self.assertEqual(snap.summary['last_write'].day, 4)

    def test_record_quoted_by_bot_does_not_become_bot_original(self):
        ground = {'now_ts': NOW.timestamp(), 'model': [{'text': f'你寫「{QUOTE}」。',
                  'ts': NOW.timestamp() - 3600}], 'user': ['我今天還沒吧？'],
                  'records': [{'text': QUOTE}]}
        answer = f'你說「{QUOTE}」。'
        self.assertEqual(monitor._quote_speaker_fix(answer, ground), (answer, False))
        monitor._TURN.update({'speaker_ground': ground, 'paced_bubbles': True})
        sent = []
        client = SimpleNamespace(send=lambda s: sent.append(s) or True, dry_run=True)
        monitor._say(client, answer)
        monitor._remember(self.state, 'model', answer)
        self.assertNotIn('我約', ''.join(sent))
        self.assertEqual(self.state.convo_history[-1]['text'], ''.join(sent))

    def test_unknown_or_shared_quote_is_not_falsely_reassigned(self):
        for records in ([{'text': QUOTE}], []):
            g = {'model': [QUOTE], 'user': [QUOTE], 'records': records}
            answer = f'你說「{QUOTE}」。'
            self.assertEqual(monitor._quote_speaker_fix(answer, g), (answer, False))

    def test_generation_gets_calendar_day_and_source_boundaries(self):
        records = [{'ts': '2026-09-04T01:24:00Z', 'text': QUOTE}]
        data = {'records': records}
        brief = coach.build_memory_brief(data, self.snapshot(records), TZ, now=NOW)
        self.assertIn('今天截至資料快照有 0 則', brief)
        self.assertIn('對話史只能證明當時說過什麼', brief)
        self.assertIn(QUOTE, brief)
        c = coach.Coach(SimpleNamespace(gemini_api_key='offline-test'))
        with mock.patch.object(coach.gemini, 'generate_chat', return_value='我把日期說混了。') as generate:
            c.ask('你回頭看看你自己在說什麼', brief, {}, [], evidence_tools=False)
        args = generate.call_args.args
        self.assertIn('不是只重述一遍就宣布沒錯', args[2])
        self.assertIn('今天截至資料快照有 0 則', str(args[3]))

    def test_nested_turn_restores_all_outer_facts_even_after_failure(self):
        outer_ground = {'today_count': 0, 'last_label': '昨天 09:24'}
        def inner(update, *args):
            if update.get('inner'):
                monitor._TURN['write_claim_ground'] = {'today_count': 1}
                monitor._TURN['speaker_ground'] = {'model': ['錯誤外溢']}
                monitor._TURN['coord_claim_truth'] = (0.9, 0.9)
                raise RuntimeError('nested')
            monitor._TURN.update({'write_claim_ground': outer_ground,
                                  'speaker_ground': {'model': ['原本的話']},
                                  'coord_claim_truth': (0.1, 0.2)})
            with self.assertRaises(RuntimeError):
                monitor.handle_message({'inner': True}, *args)
            self.assertEqual(monitor._TURN['write_claim_ground'], outer_ground)
            self.assertEqual(monitor._TURN['speaker_ground'], {'model': ['原本的話']})
            self.assertEqual(monitor._TURN['coord_claim_truth'], (0.1, 0.2))
        cfg = SimpleNamespace(burst_paced_bubbles_enabled=True)
        with mock.patch.object(monitor, '_handle_message_inner', side_effect=inner):
            monitor.handle_message({}, None, None, None, None, self.state, None, cfg, TZ)

    def test_failed_bubble_stops_tail_and_memory_tracks_only_delivery(self):
        for raises in (False, True):
            monitor._TURN.clear()
            monitor._TURN['paced_bubbles'] = True
            calls = []
            def send(text):
                calls.append(text)
                if len(calls) == 2:
                    if raises:
                        raise OSError('offline')
                    return False
                return True
            client = SimpleNamespace(send=send, dry_run=True)
            text = '我先核對昨天的原始紀錄。今天這一筆還沒有證據。不能用昨天反駁今天。'
            self.assertFalse(monitor._say(client, text))
            self.assertEqual(len(calls), 2)
            monitor._remember(self.state, 'model', text)
            self.assertEqual(self.state.convo_history[-1]['text'], calls[0])

    def test_persona_does_not_require_fabricated_inner_experience(self):
        system = persona.SOCRATIC_SYSTEM
        self.assertNotIn('背叛你自己', system)
        self.assertIn('不足以推斷他很忙', system)
        self.assertIn('狀態沒變、沒有新觀察時可以安靜', system)
        self.assertIn('不能為維持角色而否認', system)

    def test_successful_retry_replaces_failed_proactive_memory_mapping(self):
        text = '這是有來源的新觀察。'
        failed = SimpleNamespace(send=lambda _: False, dry_run=True)
        succeeded = SimpleNamespace(send=lambda _: 123, dry_run=True)
        monitor._say(failed, text, prefix='🔮 ', state=self.state, topic='觀察')
        monitor._say(succeeded, text, prefix='🔮 ', state=self.state, topic='觀察')
        monitor._remember(self.state, 'model', '🔮 ' + text)
        self.assertIn(text, self.state.convo_history[-1]['text'])

    def test_runtime_summary_exposes_disabled_guards_without_credentials(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            cfg = config.Config.load(env_file='/dev/null')
        summary = monitor._dialogue_runtime_summary(cfg)
        self.assertIn('觀點修正台帳=off', summary)  # New core is opt-in during rollout.
        self.assertNotIn('off', summary.replace('觀點修正台帳=off', ''))
        cfg.burst_paced_bubbles_enabled = False
        self.assertIn('長短泡泡=off', monitor._dialogue_runtime_summary(cfg))

    def test_proactive_voice_uses_pacing_without_interactive_turn(self):
        self.state.PACED_DIALOGUE = True
        text = ('我回頭看了這兩筆不同日期的記寫，發現它們其實在談同一個問題，'
                '只是當時採用的做法和現在不一樣，而且原始文字也保留了這個差別，'
                '這是我這次想接著談的具體地方。')
        sent = []
        client = SimpleNamespace(send=lambda s: sent.append(s) or True, dry_run=True)
        monitor._say(client, text, state=self.state, prefix='💡 ', topic='兩筆記寫')
        self.assertGreater(len(sent), 1)
        self.assertEqual(''.join(sent), '💡 ' + text)
