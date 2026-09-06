# Mixed burst delivery regression

Previously, any captured data segment disabled pacing for the entire combined
answer. Exact data blocks now remain intact while surrounding voice is paced
using the existing sentence splitter and typing delays. Missing metadata or
transformed/unlocatable data retains the conservative fallback. The six-bubble
voice cap applies per voice region, not to protected data.

Regression coverage includes real `_say` capture through the burst transaction,
mixed and voice-only delivery, content conservation, typing delays, and stopping
after a send failure. Telegram sends and generation are mocked: these checks do
not establish the remote Mini configuration or prove the screenshot's precise
runtime cause. They also do not evaluate repetition in generated prose.
