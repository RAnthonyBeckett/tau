# How TAU talks to people

TAU is built with neurodivergent people in mind, and people with chronic pain or fatigue, and these rules make it easier for everyone else too. They apply to the installer, `tau setup`, `tau check`, the display, error messages and TAU's own replies. `tests/test_plain_words.py` checks the parts a test can check.

## The rules

**One question at a time.** Setup asks one thing per screen line and waits. A menu only shows "recommended" when there's a real reason, and the reason is given in a few words. With no reason, nothing is pre-picked and you type a number. Every question can be skipped, and you can answer it later with `tau setup <topic>`.

**No wait over 3 seconds without a reason and an estimate.** Before anything slow, TAU says what it's about to do and roughly how long it takes. During it, a new line or the display's status line updates at least every 15 seconds, so the screen never sits frozen. Downloads show how much is done and about how long is left.

**No library noise.** Warnings, progress bars and tracebacks from other software go to TAU's log file (`<TAU home>/log/tau.log`, and the installer's own log under the install folder). The screen gets one plain sentence. Set `TAU_DEBUG=1` to see everything.

**Every failure says three things:** what broke, the one next step, and where the log is. For example: "Stopped: Installing OpenJarvis from PyPI didn't work. Next: Check your internet connection, then run this script again. The details are in ...".

**No hidden controls.** Everything you can do on the display has a visible button or a labelled piece you can hover over: Talk, the type box, Pin cards, help (?), Hands-free, the corner switches and the organ ticks. Keyboard shortcuts (Space to talk, / to type, Esc to stop) are extras, never the only way. The first time the display opens it names each control once; "?" shows that again.

**Nothing to remember between screens.** Each message stands on its own. Answers that need keeping go on a pin card, which stays until you clear it. Nothing disappears on a timer.

**Plain words.** Short sentences, no jargon where a plain word works, and none of the words in `banned_words.example.txt`. Numbers are rounded unless the exact figure matters.

**The user sets the pace.** TAU never pushes to the next step, and it asks rather than assuming you're done with something. The optional pacing profile (`tau setup pacing`) adds your own rules on reply length, lists and more, plus a one-click "hard day" switch (the display's top-right corner).

## Privacy, said plainly

Hands-free listening is off by default. When it's on, TAU checks everything the microphone hears for speech and turns any speech into text on this computer, so nearby talk may be picked up. Nothing is saved or sent anywhere until it becomes a message.

Anything you write about why you need these rules stays on this computer and is never sent to any model. Only the neutral rules themselves ("keep replies under three paragraphs") go into a prompt.
