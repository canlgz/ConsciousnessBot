"""Synthetic records -> real association -> delivery -> correction -> reload.

Only model wording and Telegram transport are fakes. No private records/network.
"""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telegram_monitor import monitor, viewpoint
from telegram_monitor.state import State

NOW = datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc)
TS = NOW.timestamp()


class Client:
    dry_run = False

    def __init__(self, fail_at=0):
        self.sent, self.calls, self.typings = [], 0, 0
        self.fail_at = fail_at

    def send(self, text):
        self.calls += 1
        if self.calls == self.fail_at:
            return False
        self.sent.append(text)
        return 1000 + self.calls

    def send_typing(self):
        self.typings += 1


class ViewpointPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(str(Path(self.tmp.name) / 'state.json'))
        self.state.owner_folder_id = 'F'
        self.cfg = SimpleNamespace(
            conscious_dialogue_enabled=True, association_enabled=True,
            association_easy=True, association_seed_goal=False,
            selfstate_sensitivity=2.0, timezone='Asia/Taipei',
            burst_one_answer_enabled=True, burst_paced_bubbles_enabled=True,
            dry_run=False, telegram_chat_id='1', mood_gain=1.0)
        self.records = []
        for topic, vector, text in (
            ('教學', [1., .3, 0.], '我把教學進度拆成小步驟，想看看學生能否接上。'),
            ('跑步', [.5, 1., 0.], '我把跑步距離拆成小段，先試試膝蓋的負荷。')):
            for i in range(4):
                self.records.append({'id': f'{topic}-{i}', 'topicLabel': topic,
                                     'text': text + str(i), 'ts': TS - 100 + i,
                                     'embedding': vector + [0.] * 5})
        self.reader = SimpleNamespace(load_embedding_records=lambda _: self.records)
        self.coach = SimpleNamespace(enabled=True, voice_insight=Mock(return_value=
            '我把「教學」和「跑步」放在一起看：兩筆都提到拆小步驟。'
            '也許都在用小規模嘗試降低一次承擔的負荷，這是我的假設。'))
        self.client = Client()
        saved = copy.deepcopy(monitor._TURN)
        monitor._TURN.clear()
        self.addCleanup(lambda: (monitor._TURN.clear(), monitor._TURN.update(saved)))
        self.sleep = patch.object(monitor, '_sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def update(self, text, uid=1, reply=None):
        msg = {'text': text, 'date': TS + uid, 'message_id': uid, 'chat': {'id': 1}}
        if reply:
            msg['reply_to_message'] = {'message_id': reply}
        return {'update_id': uid, 'message': msg}

    def run_turn(self, update, client=None):
        monitor.handle_message(update, self.coach, self.reader,
                               {'records': self.records, 'meta': {}}, None,
                               self.state, client or self.client, self.cfg, None)

    def form(self):
        monitor._association_step(self.reader, self.state, self.cfg,
            {'now': NOW, 'data': {'meta': {'lastIngestTs': TS}}, 'self_stim_fired': False})
        self.assertIsNotNone(self.state.insight_pending)
        self.key = self.state.insight_pending['event']['view_id']
        self.event = copy.deepcopy(self.state.insight_pending['event'])
        self.assertEqual(self.view()['status'], 'candidate')

    def view(self):
        return self.state.conscious_dialogue['views'][self.key]

    def speak(self, fail_at=0):
        self.form()
        self.client = Client(fail_at)
        monitor._TURN['paced_bubbles'] = True
        monitor._insight_emit(self.client, self.state, self.cfg, self.coach, NOW)

    def reload(self):
        self.state = State.load(self.state.path)

    def test_full_loop_correction_survives_history_loss_and_restart(self):
        self.speak()
        self.assertTrue(self.view()['deliveries'])
        self.assertIn('教學', self.coach.voice_insight.call_args.args[0])
        self.assertEqual(len(self.view()['sources']), 2)
        self.assertTrue(all(s['event_ts'] < s['read_ts'] for s in self.view()['sources']))
        before = copy.deepcopy(self.state.conscious_dialogue)
        u = self.update('這個聯想不對，因為教學是在確認理解，跑步是在避免受傷')
        self.run_turn(u)
        self.assertEqual(self.view()['status'], 'rejected')
        correction = self.state.conscious_dialogue['corrections'][viewpoint.event_id(u)]
        self.assertTrue(correction['acknowledged'])
        self.assertIn('確認理解', correction['text'])
        self.state.convo_history = []
        self.state.save()
        self.reload()
        self.client.sent.clear()
        self.run_turn(self.update('你現在怎麼看教學與跑步的聯想？', 2))
        self.assertIn('已經撤回', ''.join(self.client.sent))
        self.assertIn('避免受傷', ''.join(self.client.sent))
        self.assertEqual(self.coach.voice_insight.call_count, 1)
        # Counterfactual: same question, same records, without the correction.
        self.state.conscious_dialogue = before
        self.client.sent.clear()
        self.run_turn(self.update('你現在怎麼看教學與跑步的聯想？', 3))
        self.assertIn('待核對', ''.join(self.client.sent))
        self.assertNotIn('已經撤回', ''.join(self.client.sent))

    def test_received_correction_persists_when_acknowledgement_fails(self):
        self.speak()
        u = self.update('這個聯想不對')
        self.run_turn(u, Client(fail_at=1))
        self.reload()
        self.assertEqual(self.view()['status'], 'rejected')
        change = self.state.conscious_dialogue['corrections'][viewpoint.event_id(u)]
        self.assertFalse(change['acknowledged'])
        self.run_turn(u)
        self.assertEqual(len(self.view()['revisions']), 1)
        self.assertTrue(change['acknowledged'])

    def test_unsent_draft_is_not_an_implicit_target(self):
        self.speak(fail_at=1)
        self.assertEqual(self.view()['deliveries'], [])
        self.assertIsNone(viewpoint.correction(self.state, self.update('這個聯想不對'), TS + 1))

    def test_partial_delivery_keeps_only_visible_words(self):
        self.speak(fail_at=2)
        delivery = self.view()['deliveries'][0]
        self.assertFalse(delivery['complete'])
        self.assertEqual(delivery['text'].replace('\n', ''), ''.join(self.client.sent))
        self.assertNotIn('這是我的假設', delivery['text'])

    def test_rejected_pair_stays_blocked_when_revisited_or_new_anchor_appears(self):
        self.speak()
        self.run_turn(self.update('這個聯想不對'))
        sources = copy.deepcopy(self.view()['sources'])
        viewpoint.observe(self.state, self.event, self.records, TS + 100)
        self.assertEqual(self.view()['sources'], sources)
        self.assertTrue(viewpoint.blocked(self.state, self.event))
        self.state.insight_pending = {'event': self.event, 'ts': TS + 10000}
        self.state.last_user_msg_ts = 0
        self.client.sent.clear()
        monitor._insight_emit(self.client, self.state, self.cfg, self.coach,
                             datetime.fromtimestamp(TS + 10000, timezone.utc))
        self.assertFalse(self.client.sent)

    def test_source_ambiguity_and_future_dates_do_not_create_views(self):
        self.form()
        records = copy.deepcopy(self.records)
        anchor = self.event['anchor_a']
        record = next(r for r in records if r['ts'] == anchor['ts'] and
                      r['topicLabel'] == self.event['a'])
        duplicate = dict(record, id='different-source')
        self.assertIsNone(viewpoint.observe(self.state, self.event, records + [duplicate], TS))
        self.assertIsNone(viewpoint.observe(self.state, self.event, records, TS - 1000))

    def test_unknown_reply_or_unrelated_intervening_message_does_not_retarget(self):
        self.speak()
        self.assertIsNone(viewpoint.correction(self.state, self.update('不對', reply=9999), TS + 1))
        self.state.convo_history.append({'role': 'model', 'text': '現在十二點。', 'ts': TS})
        self.assertIsNone(viewpoint.correction(self.state, self.update('不對'), TS + 1))
        self.assertIsNone(viewpoint.correction(self.state,
            self.update('我不認為教學與跑步無關'), TS + 1))
        self.assertIsNone(viewpoint.correction(self.state,
            self.update('這個聯想不對？'), TS + 1))

    def test_exact_reply_can_correct_after_restart(self):
        self.speak()
        mid = self.view()['deliveries'][0]['message_ids'][0]
        self.reload()
        self.run_turn(self.update('不對', reply=mid))
        self.assertEqual(self.view()['status'], 'rejected')

    def test_burst_correction_and_followup_are_one_answer_with_delivery_receipt(self):
        self.speak()
        parts = [self.update('這個聯想不對'), self.update('你現在怎麼看這個聯想？', 2)]
        burst = monitor.build_coalesced_update({'type': 'text', 'updates': parts})
        self.client.sent.clear()
        self.run_turn(burst)
        self.assertEqual(''.join(self.client.sent).count('已經撤回'), 1)
        self.assertEqual(len(self.view()['revisions']), 1)
        self.assertTrue(self.state.conscious_dialogue['corrections'][viewpoint.event_id(parts[0])]['acknowledged'])
        count = len(self.client.sent)
        self.run_turn(burst)
        self.assertEqual(len(self.client.sent), count)

    def test_burst_failed_delivery_does_not_rollback_correction(self):
        self.speak()
        parts = [self.update('這個聯想不對'), self.update('你現在怎麼看這個聯想？', 2)]
        burst = monitor.build_coalesced_update({'type': 'text', 'updates': parts})
        with self.assertRaises(monitor.BurstDeliveryError):
            self.run_turn(burst, Client(fail_at=1))
        self.reload()
        self.assertEqual(self.view()['status'], 'rejected')
        self.assertEqual(len(self.view()['revisions']), 1)
        self.run_turn(burst)
        self.assertIn('已經撤回', ''.join(self.client.sent))

    def test_flag_off_preserves_existing_ledger_without_using_it(self):
        self.speak()
        original = copy.deepcopy(self.state.conscious_dialogue)
        self.cfg.conscious_dialogue_enabled = False
        with patch.object(monitor, '_handle_message_inner') as legacy:
            self.run_turn(self.update('這個聯想不對'))
        legacy.assert_called_once()
        self.assertEqual(self.state.conscious_dialogue, original)
        self.state.save()
        self.reload()
        self.assertEqual(self.state.conscious_dialogue, original)

    def test_unknown_schema_is_preserved(self):
        self.state.conscious_dialogue = {'version': 99, 'opaque': 'keep'}
        self.state.save()
        self.reload()
        self.assertIsNone(viewpoint.ledger(self.state, create=True))
        self.assertEqual(json.loads(Path(self.state.path).read_text())['conscious_dialogue']['version'], 99)

    def test_followup_pronoun_stays_bound_after_ack_and_reload(self):
        self.speak()
        self.run_turn(self.update('這個聯想不對'))
        self.reload()
        self.client.sent.clear()
        self.run_turn(self.update('你現在怎麼看這個聯想？', 2))
        self.assertIn('已經撤回', ''.join(self.client.sent))
        self.assertGreater(self.client.typings, 0)
        monitor._sleep.assert_called()

    def test_compound_requests_are_not_consumed_as_recall(self):
        self.speak()
        for text in ('你怎麼看教學與跑步？另外十分鐘後提醒我',
                     '你現在怎麼看教學與跑步，順便列出今天記寫',
                     '明天告訴我你怎麼看教學與跑步',
                     '/cost 教學 跑步 怎麼看'):
            self.assertEqual(viewpoint.answer(self.state, self.update(text), TS + 1), '')

    def test_disabled_new_install_does_not_create_a_ledger(self):
        self.cfg.conscious_dialogue_enabled = False
        monitor._association_step(self.reader, self.state, self.cfg,
            {'now': NOW, 'data': {'meta': {'lastIngestTs': TS}}, 'self_stim_fired': False})
        self.assertIsNotNone(self.state.insight_pending)
        self.assertIsNone(self.state.conscious_dialogue)
        self.state.save()
        self.assertNotIn('conscious_dialogue', json.loads(Path(self.state.path).read_text()))

    def test_future_records_are_filtered_before_association_computation(self):
        for record in self.records:
            record['ts'] = TS + 100
        monitor._association_step(self.reader, self.state, self.cfg,
            {'now': NOW, 'data': {'meta': {'lastIngestTs': TS}}, 'self_stim_fired': False})
        self.assertIsNone(self.state.insight_pending)
        self.assertIsNone(self.state.conscious_dialogue)

    def test_foreign_chat_cannot_correct_a_view(self):
        self.speak()
        update = self.update('這個聯想不對')
        update['message']['chat']['id'] = 999
        self.run_turn(update)
        self.assertEqual(self.view()['status'], 'candidate')

    def test_exact_quote_targets_only_the_delivered_view(self):
        self.speak()
        quoted = self.view()['deliveries'][0]['text']
        self.state.convo_history = []
        self.run_turn(self.update(f'「{quoted}」不對'))
        self.assertEqual(self.view()['status'], 'rejected')

    def test_explicit_reversal_is_preserved_without_promoting_hypothesis_to_fact(self):
        self.speak()
        rejection = self.update('這個聯想不對')
        self.run_turn(rejection)
        self.assertIsNone(viewpoint.correction(self.state, self.update('這個聯想有道理', 2), TS + 2))
        self.run_turn(self.update('我收回之前的否定，這個聯想有道理', 3))
        self.assertEqual(self.view()['status'], 'supported_by_user')
        self.assertFalse(viewpoint.blocked(self.state, self.event))
        self.reload()
        self.client.sent.clear()
        self.run_turn(rejection)  # Replayed old update must not restore the old stance.
        self.assertEqual(self.view()['status'], 'supported_by_user')
        self.assertNotIn('我撤回', ''.join(self.client.sent))
        self.assertIn('仍需要具體證據', ''.join(self.client.sent))

    def test_observed_association_answers_activity_with_source_content_before_delivery(self):
        self.cfg.evidence_guard_enabled = True
        self.form()
        self.reload()
        self.run_turn(self.update('你剛剛在做什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('教學', wire)
        self.assertIn('跑步', wire)
        self.assertIn('拆成小', wire)
        self.assertIn('候選連結', wire)
        self.assertNotIn('送達紀錄不足', wire)
        self.coach.voice_insight.assert_not_called()

    def test_real_generated_thought_available_as_hypothesis_without_sent_receipt(self):
        self.cfg.evidence_guard_enabled = True
        self.speak(fail_at=1)
        self.reload()
        self.client = Client()
        self.run_turn(self.update('你剛剛在想什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('還待核對', wire)
        self.assertIn('小規模嘗試', wire)
        self.assertEqual(self.coach.voice_insight.call_count, 1)
