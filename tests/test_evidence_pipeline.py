"""Adversarial screenshot replay: real handlers, no Telegram or paid model."""
import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from telegram_monitor import evidence, activity, monitor, coach as coachmod, config
from telegram_monitor.state import State

TZ = ZoneInfo('Asia/Taipei')
NOW = datetime(2026, 9, 6, 8, 39, tzinfo=TZ).timestamp()
FABRICATION = '今天一早起來，就開始洗碗、摺衣服、打掃家裡，這些都是日常的瑣事，但也是構成生活的重要部分。雖然忙碌，但心裡感覺很充實。'


class Transport:
    dry_run = False

    def __init__(self, failure=0):
        self.sent, self.n, self.failure = [], 0, failure
        self.typing = 0

    def send(self, text):
        self.n += 1
        if self.n == self.failure:
            return False
        self.sent.append(text)
        return 100 + self.n

    def send_typing(self):
        self.typing += 1


class EvidencePipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(str(Path(self.tmp.name) / 'state.json'))
        self.state.owner_folder_id = 'F'
        self.cfg = SimpleNamespace(evidence_guard_enabled=True, dry_run=False,
            telegram_chat_id='1', timezone='Asia/Taipei', mood_gain=1.,
            burst_one_answer_enabled=True, burst_paced_bubbles_enabled=True)
        self.client = Transport()
        self.coach = Mock()
        self.coach.enabled = True
        self.data = {'records': [], 'meta': {}}
        self.snapshot = SimpleNamespace(summary={'total': 0, 'last24h': 0, 'last7d': 0},
            funnel={'candidate': 0, 'context': 0, 'journey': 0, 'watch': 0}, heartbeat={'status': 'ok'})
        saved = copy.deepcopy(monitor._TURN)
        monitor._TURN.clear()
        self.addCleanup(lambda: (monitor._TURN.clear(), monitor._TURN.update(saved)))
        self.clock = patch.object(evidence.time, 'time', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.sleep = patch.object(monitor, '_sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def update(self, text, uid=1):
        return {'update_id': uid, 'message': {'chat': {'id': 1}, 'message_id': uid,
                'date': NOW, 'text': text}}

    def handle(self, update, client=None):
        monitor.handle_message(update, self.coach, None, self.data, self.snapshot,
                               self.state, client or self.client, self.cfg, TZ)

    def test_screenshot_multiturn_cannot_bootstrap_fabricated_history(self):
        self.state.convo_history = [
            {'role': 'model', 'text': '我昨天早上8:14跟你說過「早安，這個日常寫得真好」。', 'ts': NOW-900},
            {'role': 'model', 'text': '它就在我們對話紀錄裡。', 'ts': NOW-800}]
        turns = ['這是你第一次主動跟我說早安。', '昨天的是你自己「主動」做的嗎',
                 '你自己真的確定昨天8:14那次有說？', '那時候你指的日常是什麼事？',
                 '瞎掰。這是你llm的幻覺']
        for i, text in enumerate(turns, 1):
            self.handle(self.update(text, i))
        wire = ''.join(self.client.sent)
        self.assertNotIn('洗碗', wire)
        self.assertNotIn('早安，這個日常寫得真好', wire)
        self.assertNotIn('我確定。', wire)
        self.assertNotIn('重新檢查了', wire)
        self.assertIn('不足以判定', wire)
        self.assertIn('不能確認', wire)
        self.assertIn('我先撤回', wire)
        self.coach.ask.assert_not_called()
        self.coach.reply.assert_not_called()
        self.assertTrue(self.state.convo_history[0]['disputed'])
        self.state = State.load(self.state.path)
        self.assertEqual(len(self.state.evidence_memory['disputes']), 1)
        self.client.sent.clear()
        self.handle(self.update('你自己真的確定昨天8:14那次有說？', 6))
        self.assertNotIn('早安，這個日常寫得真好', ''.join(self.client.sent))

    def test_real_delivery_time_and_origin_are_distinct_from_text_claims(self):
        yesterday = datetime(2026, 9, 5, 8, 14, tzinfo=TZ).timestamp()
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], origin='reply', now=yesterday-60)
        with patch.object(evidence.time, 'time', return_value=yesterday):
            monitor._say(frame, '早安，今天慢慢來。')
        row = self.state.evidence_memory['deliveries'][0]
        self.assertEqual(row['ts'], yesterday)
        self.assertEqual(row['trigger_ts'], yesterday-60)
        self.client.sent.clear()
        self.handle(self.update('你確定昨天8:14跟我說過早安嗎？'))
        wire = ''.join(self.client.sent)
        self.assertIn('2026-09-05 08:14', wire)
        self.assertIn('處理你的訊息', wire)
        self.assertNotIn('自己主動', wire)

    def test_background_send_does_not_prove_autonomous_initiation(self):
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
        monitor._say(frame, '早安。', state=self.state, prefix='🫧 ')
        self.client.sent.clear()
        self.handle(self.update('你今天08:39那次早安是主動做的嗎？'))
        self.assertIn('不足以區分', ''.join(self.client.sent))

    def test_whole_generated_reply_is_rejected_before_first_bubble_and_memory(self):
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
        poisoned = '我確定。昨天早上8:14那則訊息確實是我發的。它就在我們對話紀錄裡。'
        monitor._TURN['paced_bubbles'] = True
        monitor._say(frame, poisoned)
        monitor._remember(self.state, 'model', poisoned)
        self.assertEqual(self.client.sent, [evidence.FALLBACK])
        self.assertEqual(self.state.convo_history[-1]['text'], evidence.FALLBACK)

    def test_direct_send_and_proactive_voice_share_the_guard(self):
        for direct in (False, True):
            self.client.sent.clear()
            frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
            text = f'那則記寫裡你寫到：\n> {FABRICATION}'
            if direct:
                frame.send(text)
            else:
                monitor._say(frame, text, state=self.state, prefix='💡 ')
            self.assertEqual(self.client.sent, [evidence.FALLBACK])

    def test_genuine_quote_requires_matching_record_and_date_not_corpus_union(self):
        records = [{'text': '我在閱讀經書。', 'ts': NOW-86400, 'topicLabel': '閱讀'},
                   {'text': '今天去跑步。', 'ts': NOW, 'topicLabel': '運動'}]
        self.assertIsNone(evidence.audit('你昨天寫了「我在閱讀經書。」', records, NOW, TZ))
        self.assertIsNotNone(evidence.audit('你今天寫了「我在閱讀經書。」', records, NOW, TZ))
        self.assertIsNotNone(evidence.audit('你寫了「我在閱讀經書。今天去跑步。」', records, NOW, TZ))
        self.assertIsNotNone(evidence.audit('你寫了「不存在的句子」', records, NOW, TZ))

    def test_bot_retelling_is_not_evidence_for_user_quote(self):
        history = [{'role': 'model', 'text': '洗碗很充實', 'ts': NOW-10}]
        self.assertIsNotNone(evidence.audit('你說過「洗碗很充實」', [], NOW, TZ, history))
        history.append({'role': 'user', 'text': '洗碗很充實', 'ts': NOW-5})
        self.assertIsNone(evidence.audit('你說過「洗碗很充實」', [], NOW, TZ, history))

    def test_guard_does_not_replace_uncertainty_with_false_absence(self):
        self.assertIsNotNone(evidence.audit('你那天只寫了心情，沒有寫任何日常。', [], NOW, TZ))
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
        frame.send('你那天只寫了心情，沒有寫任何日常。')
        self.assertNotIn('你那天只寫', ''.join(self.client.sent))

    def test_partial_failure_records_only_real_receipts_and_survives_reload(self):
        client = Transport(failure=2)
        frame = monitor._evidence_wrap(client, self.state, self.cfg, [], now=NOW)
        monitor._TURN['paced_bubbles'] = True
        monitor._say(frame, '我先把這個問題留著。還需要更多能核對的線索。')
        self.state = State.load(self.state.path)
        self.assertEqual(len(self.state.evidence_memory['deliveries']), 1)
        self.assertEqual(self.state.evidence_memory['deliveries'][0]['text'], client.sent[0])

    def test_burst_receipts_and_context_survive_commit(self):
        parts = [self.update('你確定昨天8:14說過早安嗎？', 1),
                 self.update('那時候你指的日常是什麼事？', 2)]
        burst = monitor.build_coalesced_update({'type': 'text', 'updates': parts})
        self.handle(burst)
        self.assertTrue(self.state.evidence_memory['deliveries'])
        self.assertIsNotNone(self.state.evidence_memory['context'])
        count = len(self.client.sent)
        self.handle(burst)
        self.assertEqual(len(self.client.sent), count)

    def test_default_enabled_independent_of_conscious_dialogue(self):
        with patch.dict('os.environ', {}, clear=True):
            cfg = config.Config.load(env_file='/dev/null')
        self.assertTrue(cfg.evidence_guard_enabled)
        self.assertFalse(cfg.conscious_dialogue_enabled)

    def test_history_input_marks_disputed_model_text_as_unverified(self):
        cfg = SimpleNamespace(gemini_api_key='offline')
        c = coachmod.Coach(cfg)
        contents = c._history_contents('', [{'role': 'model', 'text': FABRICATION,
                                            'ts': NOW-1, 'disputed': True}], now_ts=NOW)
        self.assertIn('不可用作事實來源', str(contents))

    def test_ordinary_hypotheses_and_discussion_of_hallucination_are_not_history_queries(self):
        self.assertFalse(evidence.historical_request('什麼是LLM幻覺？'))
        self.assertIsNone(evidence.audit('我想到一個假設：也許可以先做小規模嘗試。', [], NOW, TZ))

    def test_correction_survives_failed_burst_acknowledgement(self):
        self.state.convo_history = [{'role': 'model', 'text': FABRICATION, 'ts': NOW-30}]
        parts = [self.update('瞎掰。這是你的幻覺', 10), self.update('你確定昨天說過嗎？', 11)]
        with self.assertRaises(monitor.BurstDeliveryError):
            self.handle(monitor.build_coalesced_update({'type': 'text', 'updates': parts}), Transport(failure=1))
        reloaded = State.load(self.state.path)
        self.assertTrue(reloaded.convo_history[0]['disputed'])
        self.assertEqual(len(reloaded.evidence_memory['disputes']), 1)
        self.assertEqual(reloaded.evidence_memory['deliveries'], [])

    def test_rejection_is_not_success_and_suppresses_sticker_and_attachment(self):
        base = Mock(dry_run=False)
        base.send.return_value = 123
        frame = monitor._evidence_wrap(base, self.state, self.cfg, [], now=NOW)
        self.assertFalse(monitor._say(frame, '我確定。昨天8:14我說過早安。'))
        self.assertFalse(frame.send_sticker('sticker'))
        self.assertFalse(frame.send_file('file', b'offline', 'test.txt', 'caption'))
        base.send_sticker.assert_not_called()
        base.send_file.assert_not_called()
        self.assertEqual(self.state.evidence_memory['deliveries'][0]['text'], evidence.FALLBACK)

    def test_certified_report_does_not_hide_appended_fabricated_blockquote(self):
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
        report = '目前沒有可核對的送達紀錄。'
        frame.certify(report)
        self.assertEqual(frame.validate_voice(report + '\n那則記寫的內容如下：\n> ' + FABRICATION), evidence.FALLBACK)

    def test_real_quote_does_not_validate_wrong_category_or_absolute_date(self):
        records = [{'text': '今天去跑步。', 'ts': NOW, 'topicLabel': '運動'},
                   {'text': '讀一本書。', 'ts': NOW, 'topicLabel': '閱讀'}]
        self.assertIsNotNone(evidence.audit('它被歸類在「閱讀」，你寫了「今天去跑步。」', records, NOW, TZ))
        self.assertIsNotNone(evidence.audit('你在2026-09-05寫了「今天去跑步。」', records, NOW, TZ))
        self.assertIsNone(evidence.audit('你在2026-09-06寫了「今天去跑步。」', records, NOW, TZ))

    def test_final_burst_rejection_discards_speculative_domain_state(self):
        parts = [self.update('你昨天說過嗎？', 20), self.update('那句話是什麼？', 21)]
        def unsafe_handler(update, coach, reader, data, snap, state, client, cfg, tz):
            state.last_insight = 'UNDELIVERED_ACTION'
            client.send('我確定。昨天8:14我說過早安。')
        with patch.object(monitor, '_handle_message_inner', side_effect=unsafe_handler):
            self.handle(monitor.build_coalesced_update({'type': 'text', 'updates': parts}))
        self.assertNotEqual(self.state.last_insight, 'UNDELIVERED_ACTION')
        self.assertEqual(self.client.sent, [evidence.FALLBACK])
        self.assertEqual(self.state.convo_history[-1]['text'], evidence.FALLBACK)

    def test_activity_question_answers_completed_scan_through_real_collection(self):
        self.cfg.heartbeat_stall_grace_h = 24
        reader = SimpleNamespace(load_owner_data=lambda _: self.data)
        with patch.object(monitor.analyzer, 'analyze', return_value=self.snapshot):
            monitor._collect(reader, self.state, self.cfg, TZ, datetime.fromtimestamp(NOW-20, TZ))
        self.handle(self.update('你剛剛在做什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('讀取了你的記寫', wire)
        self.assertNotIn(evidence.FALLBACK, wire)
        self.assertNotIn('沒做', wire)
        self.coach.reply.assert_not_called()
        self.assertIn('讀取了你的記寫', self.state.convo_history[-1]['text'])

    def test_no_activity_does_not_infer_inactivity_from_last_chat(self):
        self.state.convo_history = [{'role': 'model', 'text': '早安', 'ts': NOW-3360}]
        self.handle(self.update('你剛剛在做什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('現在在讀你這則訊息', wire)
        self.assertNotIn('56', wire)
        self.assertNotIn(evidence.FALLBACK, wire)
        self.assertNotIn('沒有任何動作', wire)

    def test_explain_delivered_guard_without_inventing_historical_reason(self):
        frame = monitor._evidence_wrap(self.client, self.state, self.cfg, [], now=NOW)
        frame.send('我確定。昨天8:14我說過早安。')
        self.client.sent.clear()
        self.handle(self.update('你說的' + evidence.FALLBACK + '是什麼', 2))
        wire = ''.join(self.client.sent)
        self.assertIn('制式句', wire)
        self.assertNotIn('56 分鐘', wire)
        self.assertNotIn('沒有任何動作紀錄', wire)
        self.coach.reply.assert_not_called()

    def test_activity_burst_and_restart_keep_real_event_time(self):
        activity.record(self.state, self.cfg, 'revisit', NOW-1200, topic='閱讀')
        self.state = State.load(self.state.path)
        parts = [self.update('你剛剛在做什麼', 30), self.update('為什麼', 31)]
        self.handle(monitor.build_coalesced_update({'type': 'text', 'updates': parts}))
        wire = ''.join(self.client.sent)
        self.assertIn('09/06 08:19', wire)
        self.assertIn('背景輪替', wire)
        self.assertNotIn(evidence.FALLBACK, wire)

    def test_activity_route_does_not_swallow_other_people_or_future_tasks(self):
        for text in ('我剛剛在做什麼', '你昨天8:14在做什麼', '你明天提醒我做什麼', '/cost'):
            self.assertFalse(activity.interested(text))

    def test_activity_hypothesis_respects_later_rejection(self):
        activity.record(self.state, self.cfg, 'thought', NOW-20, a='教學', b='跑步', text='未核對想法')
        self.state.assoc_feedback = {'教學|跑步': {'sentiment': 'reject'}}
        self.handle(self.update('你剛剛在想什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('受到你的質疑', wire)
        self.assertNotIn('未核對想法', wire)

    def test_activity_snapshots_accept_datetime_sources_and_are_not_future_activity(self):
        refs = activity.sources([{'ts': datetime.fromtimestamp(NOW-30, TZ), 'text': '讀經進度', 'topicLabel': '閱讀'}], NOW)
        activity.record(self.state, self.cfg, 'scan', NOW-10, sources=refs)
        activity.record(self.state, self.cfg, 'revisit', NOW+10, topic='不該提前知道')
        self.state = State.load(self.state.path)
        self.handle(self.update('你剛剛在做什麼'))
        wire = ''.join(self.client.sent)
        self.assertIn('讀經進度', wire)
        self.assertNotIn('不該提前知道', wire)

    def test_routine_comparison_does_not_hide_recent_concrete_thought(self):
        activity.record(self.state, self.cfg, 'thought', NOW-40, a='教學', b='跑步', text='也許可以比較兩種小步嘗試。')
        activity.record(self.state, self.cfg, 'compare', NOW-20, count=8)
        activity.record(self.state, self.cfg, 'scan', NOW-10)
        self.handle(self.update('你剛剛在想什麼'))
        self.assertIn('兩種小步嘗試', ''.join(self.client.sent))
