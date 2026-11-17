# Changelog

All notable changes to TAU. Dates are when each round was built.

## 1.3.0 (round 6, 2026-11-05)

1.2.0 ran on the owner's PC but couldn't be pointed at real work. 1.3.0 adds the first everyday path:
add a folder, connect a worker, ask, send, and get a result back. Tested end to end with a fake worker
only; Claude Code and Codex are detected and their command-line options checked, but no real job was run.

### Work
- Five buttons on the display: **Add a folder**, **Add an app**, **Set up work**, **My work**, **Check TAU**.
  Saying or typing "add a folder", "connect Claude", "set up work", "my work" or "how is it going" opens the same flows.
- **Add a folder**: the system folder dialog where the machine has one, a browse-here list (home folder and drives,
  one level at a time) and a paste-a-path fallback. Then one question: "Read for context" or "Let a worker change files here".
  Reading is the default. TAU shows the project's AGENTS.md, CLAUDE.md and STATE.md and any `work.house_rules` files.
  Keys, `.env` files and hidden settings folders are never read or sent. Forgetting a folder never touches its files.
- **Add an app**: Claude Code and Codex are found if installed, and their help text is checked for the options TAU
  uses. Found isn't Ready: TAU can't tell if you're signed in or have credit without a paid request, and says so.
  OpenRig is listed as Unavailable or Needs connection; TAU never sends work through it in this version.
- **Set up work**: a draft with a one-line summary ("Claude Code will work in Comic on this PC, using its project
  rules. I'll bring back the result.") and the seven details underneath. Send is the yes. Changing the draft after
  you looked at it means Send asks you to look again. A missing project or worker is asked for, one at a time.
- "Have Claude work on this ..." makes a draft; "send it" sends it. "Could we", "I'm thinking", "I don't know",
  "maybe" and questions never make or send a job.
- **My work**: each job's state (Draft, Running, Finished, Failed, Cancelled, Status unknown), its result, Cancel,
  Retry and Dismiss. A finished job leaves one message and one pin card. Exit 0 with a missing expected file is
  reported as "check it", not "all done". A job running when TAU restarts becomes Status unknown, never Finished.
- Claude Code runs with `--permission-mode plan` (read-only) or `acceptEdits` (may change files in the project
  folder) and never with a bypass flag. Codex runs with `--sandbox read-only` or `workspace-write`. A custom worker
  TAU can't limit stays a draft until you tick "I understand".
- A folder on another machine is recorded but never read or run on this one. Sending work to another machine
  isn't built yet and says so.

- "Ask <app> to <action>" drives an app that offers TAU's connection (describe, status, do on this computer):
  only the actions it lists, with your yes, and its own reported result. HTTP 200 alone is never "done".
- A follow-up to a finished Claude Code job goes back into the same Claude conversation (`--resume`).
- "Where were we?" or "what's unfinished?" gives a one-line reminder. Heavy requests make a draft, never a launch.

### Check TAU and connections
- Read-only checks of the brain, sound devices, the selected voice and its pronunciation model, folders, workers and
  lab machines, each with an 8-second limit and a next step. A British voice without the British pronunciation
  model is not Ready. It lists what it didn't check (microphone and speaker tests, worker sign-in, remote work).

### Listening, labelled honestly
- Hands-free is renamed **Listen to all speech** (it was never a wake word). Still off unless you switch it on.
  With no wake model, the microphone line says "hotword unavailable, use Talk or press Space". A real "Tau" hotword is not in this release.

### Permissions
- New `work` capability. A profile without it gets Suggest: every send needs your Send or "send it".
  No existing grant is widened.
- README: Act was described as asking first; it doesn't. Fixed to match what the code enforces.

## 1.2.0 (round 5, 2026-11-05)

1.1.0 ran on the owner's Windows 11 PC and saw all four lab machines, but no speaker or microphone choice played sound, nicknames like "Hal" didn't resolve, and the display needed the owner's rulings. 1.2.0 fixes those, in that order.

### Sound that works
- Settings, Voice and hearing lists each physical speaker and microphone once (WASAPI preferred, MME as the fallback), with **System default** first and chosen by default. Picking one lights only that one.
- A choice is saved as audio system, number and name, and matched exactly; a name saved by 1.1 still works.
- Speaker and microphone open in shared mode at a rate the device accepts. Kokoro's 24 kHz voice is converted to the speaker's rate and the microphone's rate to 16 kHz for hearing.
- **Test the speaker** plays the chime on the chosen device and shows "played" or the exact failure. **Test the microphone** shows a live level for three seconds.
- If the chosen speaker fails while TAU speaks, she uses the system default and says so on the display. New choices take effect without a restart.

### Voice failures are visible
- Any speaking failure flashes the display and pins a card with the plain reason; the reply stays on screen as text.
- Picking a voice that isn't on this computer downloads it then (progress on the status line) and plays a short sample. `tau fetch-models` downloads every voice Settings offers, and both unknown-word helpers.

### Names and nicknames
- Machine names match in any case, by a unique prefix ("Hal" for Halcyon), by the short hostname, and by nicknames set in Settings, Lab (filled at pairing with the short hostname). One resolver serves every lab question and the system prompt's machine list. An unknown name gets the list of known machines.

### Display (the owner's rulings)
- The number tags on the rings are gone. Ring pieces stay clickable with hover detail.
- **What I see** is a slide-out panel behind a tab at the left edge, closed by default and remembered open or closed, with sections that open and close: Status, Machines, Pins, Recent actions. Triangle, rings, colours, warning flashes and the speaking pulse are unchanged.

### Receipts
- Every permission change (Settings or voice) and every action at suggest or act level writes a receipt to `log/actions.jsonl`: what, when, which machine, who approved, including "you said no" and approved actions that failed. Recent actions in the side panel reads them back.
- A suggestion waiting for a yes shows as a card with **Yes** and **No**.

### Port owners on Linux nodes
- `tau node --setup-port-owners` prints one sudoers line that allows only `ss -tlnpH` without a password and, with a yes, installs it with your own sudo after `visudo -c`. Discovery then names the program on every port (Plex's 32400, for example). TAU never asks for or stores a password, and never tries sudo before setup.

## 1.1.0 (round 4, 2026-11-05)

TAU 1.0.0 installed and ran on the owner's Windows 11 PC, but the display gave nothing to use: no settings, no readable values, nothing obvious. In 1.1.0 the display is the whole product. After install you never need a terminal.

### Settings on the display
- A **Settings** button opens one panel with eight sections, one screen each: Brain, What I watch, Permissions, Voice and hearing, Name and how I speak to you, Pacing profile, Display, and Lab. Every row shows its current value. Every change is saved at once, takes effect in the running TAU, and the status line says what changed.
- Brain: every model server and every model, with size, MoE or dense (from the name or the model file's metadata), whether it fits and whether it's in memory. **Use this** switches after a test message; **Test the brain now**.
- What I watch: each sense as a switch with one line on what it reads; services with add (by program name, port or address) and remove; **Look for services** suggests what's running.
- Permissions: four buttons per organ (Off, Look, Suggest, Act). Act asks for a yes inside the panel.
- Voice and hearing: voice choice with **Play a sample**, microphone and speaker pickers with a level test and a speaker test, hands-free with its privacy sentence, and the wake word's status and what training needs.
- Display: pop-out corner and screen, **Open the pop-out now**, the display's port. Lab: role, hub port, paired machines, and **Pair a machine** with the code on screen; a node can join from its own Settings.
- Settings writes the profile through the same code as `tau setup`, which stays for scripts.

### Readable rings
- "What I see" beside the rings writes out every reading ("GPU: 6.1 GB free", "llama-server: up", "Drive C: 41% free", "Notifications: 3 recent", "Brain: gemma-4-26B, answering"), numbered to match a badge on each ring piece.
- Switched-off pieces say "off — turn on in Settings" and a click opens that section. A click on any piece opens a details card: what it is, when it was last checked, Check now, Open in Settings, and Start/Stop for a service you allowed. A plain click no longer starts or stops a service.
- No text on the display is under 14 px; the "?" panel names every control.

### Conversation and status line
- Both sides of the conversation with times, a Copy button per message, pin cards inline, and the message being spoken marked "Speaking now".
- The status line covers every moment: listening, working out what you said, thinking with the model's name, speaking (quoting the words), ready, and model loads ("Loading gemma-4-26B, about a minute").

### Speaking
- The yellow sparks that crackled while TAU spoke are gone. Instead the rings brighten and swell slightly in time with her voice, then settle back when she stops. Colours, triangle and warning flashes are unchanged. Picture: `results/tau_r4_speaking_pulse.png`.

### Fixed
- Setup's questions could be invisible (they went to the log with stderr); now on stdout, with a test.
- Black console windows flashed every 30 seconds under the Start-menu launcher; every helper command now runs without a window, with a test.
- The Start-menu shortcut pointed into the Administrator account's folder because the installer ran in a "Run as administrator" window signed in as that account. The installer now refuses when its account isn't the one signed in at the desktop (`-AllowElevated` overrides), the launcher refuses a target in another profile, and CI checks the shortcut sits under the installing user's profile.
- Services were saved by temporary ports (49152 and up) and flashed "down" after every restart. Discovery now uses the listening parent's fixed port (a llama.cpp router's 8080) or leaves them out; old profiles are repaired at start, and TAU says so.
- The display and hub used random ports. They now use fixed ports (47110 and 47111 unless taken or changed), asked at setup and shown in Settings, never 0 or temporary.
- Model lists now say MoE or dense.

## 1.0.0 (round 3, 2026-11-05)

The first version meant for people other than its builder. It came from the first real install on a Windows 11 PC, which worked but took over an hour and hit thirteen problems. Every one is fixed here.

### Install
- One command on Windows (Windows PowerShell 5.1, which ships with Windows, or PowerShell 7) and on Linux. No admin rights, Python, git or Rust needed beforehand.
- TAU now has its own Python 3.12 environment, made with a private copy of uv. OpenJarvis comes from PyPI as plain Python. OpenJarvis's own installer, which compiles Rust and installs Ollama, only runs if you ask for it (`-UseOpenJarvisInstaller`).
- Ollama is never required. Setup finds llama.cpp, LM Studio, Ollama, vLLM or any OpenAI-compatible server first, and only offers Ollama plus one small model if nothing is found. Installing Ollama no longer hangs on its tray app.
- The installer says how long it takes and how much it downloads before it starts (measured: 140 seconds and about 1.8 GB on a clean Windows runner), says what it's doing at least every 15 seconds, and keeps all tool output in a log file.
- The voice and hearing models download during install (`tau fetch-models`), not in the middle of the first conversation.
- `tau` goes on your PATH (Windows: a small `bin` folder, so your own `python` is untouched) and a Start-menu shortcut or apps-menu entry called TAU opens the display with no console window.
- Uninstall with `-Uninstall` / `--uninstall`. Your settings stay.
- Fixed: `install.ps1` crashed under Windows PowerShell 5.1 when a check failed.

### Setup
- The brain step always ends with a brain that answered a test message, or a clear skip. "Check again" finds a server you start while setup waits. The local option installs a model for you instead of telling you to run another command.
- "Recommended" only appears when there's a reason, and the reason is shown. Model lists are sorted by what fits your computer, not alphabetically, and server labels say how many models were found.
- A new senses step offers a starter set of things to watch (graphics card, drives, models, notifications, and services found running), in one question or one at a time. A fresh install shows real data straight away.
- A hands-free question, off by default, with its privacy trade-off in one sentence.
- Fixed: Windows notification access crashed.

### The display
- The display now holds everything TAU shows. Added: a Talk button (and Space), an always-visible type box, a status line saying what TAU is doing, the conversation in large type, a Pin cards button, and a first-run help panel that "?" reopens.
- On the triangle: ticks for each permission along the top edge, Quiet and Hard day switches in the top corners, and the bottom tip glows while pin cards wait. Everything explains itself on hover.
- Empty ring pieces show faintly what will appear there. A background check every 30 seconds (no model calls) fills the rings from services, the graphics card, drives, notifications, the brain and paired machines.
- Warnings flash the frame and stay as one line until dismissed. Microphone failures flash and pin the fix.
- `tau run --window` opens the display in its own window. The look, the sparks and the warning flashes are unchanged.

### Plain words
- Library warnings, progress bars and tracebacks go to TAU's log file. The screen gets one plain sentence, and every failure says what broke, the next step and where the log is.
- `docs/ACCESSIBILITY.md` states the rules TAU follows; a test checks every user-facing string against them.
- The README starts with what a stranger needs: what TAU is, one install command, time and size, the first conversation, the display's controls with a picture, and troubleshooting.

## 0.1.0 (round 2)

- Kokoro is the default voice again with no GPL code installed: TAU's own adapter loads Kokoro's model without its espeak pipeline and handles unknown words with an Apache-2.0 BART model.
- One-command installers for Linux and Windows (on top of OpenJarvis's installer).
- A licence gate (`tau licence-check`) that fails on GPL/AGPL packages or non-commercial models, and a generated licence table in the README.
- `tau check`: a guided hardware check, one device at a time, also by voice ("check yourself").
- Hub and node pairing moved to SPAKE2 with key confirmation and a short code.
- CI on real Windows and Linux runners.

## 0.1.0 (round 1)

- TAU built as an extension of OpenJarvis: profile and setup, permission levels ("organs"), brain lanes with the GPU rule, ears (wake word, VAD, Whisper), interruptible Kokoro voice, the reflex router, output routing to clipboard and pin cards, agent hand-off, senses, OKF knowledge and TAU's own brain files, the style guard, the communication and pacing profile with its cloud privacy notice, the triangle display, and the `tau` command.
