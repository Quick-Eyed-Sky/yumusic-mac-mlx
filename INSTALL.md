# Installing this, step by step

**This guide assumes you have never opened Terminal in your life.** Every
command is written out in full. After each one you press Return and you wait
until the prompt comes back before typing the next.

Total time: about twenty minutes, most of it a single download.

⚡ **In a hurry, and you already use a Terminal?** Everything fits in a few
lines — [jump to the short version at the bottom ⬇️](#-the-short-version).

---

## 🍏 Before you start: does your Mac qualify?

**It must be an Apple Silicon Mac.** M1, M2, M3, M4, any of them, including
the Pro / Max / Ultra variants. An older Intel Mac cannot run this at all —
MLX is Apple's own framework and it only exists on Apple's own chips.

To check: **Apple menu ( ) → About This Mac**. You are looking for a line
that says **Chip: Apple M-something**. If it says *Processor: Intel*, stop
here.

**Memory.** Not measured below 64 GB here. The model weights themselves are
small — 4.2 GB for the recommended 8-bit variant — so 16 GB should be enough
for YuMusic on its own, but that is an estimate, not a tested number.

**Disk space.** About **5 GB** for the recommended 8-bit model, plus roughly
**11 GB more** if you also want the bf16 and 4-bit variants (neither is
required — the app only offers whichever you have downloaded).

To check: **Apple menu → About This Mac → More Info → Storage**.

**macOS.** Sonoma (14) or later is the safe answer.

---

## 0️⃣ Opening Terminal, and what it is

Terminal is an app that comes with every Mac. It lets you type instructions
instead of clicking them. It looks alarming and it is not.

Press **Command-Space**, type `terminal`, press **Return**.

A window opens with a line of text ending in `%` or `$`. That is the prompt:
it means "ready, type something".

**How to use this guide:** copy one block of text, click in the Terminal
window, paste with **Command-V**, press **Return**. Then wait. A step is
finished when the `%` prompt comes back on its own.

Two things worth knowing before you are surprised by them:

- **When you type a password, nothing appears.** No dots, no stars, nothing
  at all. That is deliberate. Type it and press Return.
- **You can always close the window and start again.** Nothing here can
  damage your Mac.

---

## 1️⃣ Apple's developer tools

The `git` command is not on a fresh Mac until you ask for it. Type this:

```
xcode-select --install
```

**What happens:** either a window appears offering to install "command line
developer tools" — click **Install**, agree, wait a few minutes — or Terminal
answers `command line tools are already installed`, which is equally fine.

---

## 2️⃣ A private Python environment

```
mkdir -p ~/YuE
python3 -m venv ~/YuE/venv
source ~/YuE/venv/bin/activate
pip install --upgrade pip
pip install mlx tiktoken numpy huggingface_hub gradio
```

**What happens:** a couple of minutes of `Collecting...` / `Installing...`
lines. This creates a private Python inside `~/YuE/venv` that does not touch
the Python your Mac already has — removing it later means deleting one
folder.

**Every time you come back to a fresh Terminal window**, this environment
needs switching on again with:

```
source ~/YuE/venv/bin/activate
```

The launcher in step 5 does this for you automatically — this line only
matters if you are ever poking around by hand.

---

## 3️⃣ Downloading the model

This is the model's own repository, not mine — an Apple Silicon build of
[YuE2](https://github.com/multimodal-art-projection/YuE), about 4.2 GB for
the recommended variant:

```
cd ~/YuE
hf download ahmadw/YuE2-3B-MLX --include "*.py" --include "8bit/*" --local-dir YuE2-3B-MLX
```

**What happens:** a progress bar, for a few minutes depending on your
connection. `~/YuE/YuE2-3B-MLX` ends up holding the inference code and the
8-bit model weights.

**Want bf16 (best quality, slowest) or 4-bit (fastest, some drift) as well?**
Neither is required — 8-bit is the recommended default and the only one
tested at length here. To add one or both:

```
hf download ahmadw/YuE2-3B-MLX --include "bf16/*" --local-dir YuE2-3B-MLX
hf download ahmadw/YuE2-3B-MLX --include "4bit/*" --local-dir YuE2-3B-MLX
```

The app's **Model variant** menu only ever lists the ones actually present —
download one, two or all three, whenever you like, and it adjusts itself.

---

## 4️⃣ Downloading this front-end

Two ways. Pick one.

**The simple way, no Terminal:** at the top of this project's GitHub page,
click the green **Code** button, then **Download ZIP**. Double-click the
downloaded ZIP to unpack it. Drag the resulting folder wherever you like —
Documents, Desktop, an external drive. It does not matter where; the launcher
works out its own location.

**The Terminal way**, which makes updating easier later:

```
cd ~/YuE
git clone https://github.com/Quick-Eyed-Sky/yumusic-mac-mlx.git
```

---

## 5️⃣ Letting the launcher launch

**If you used the ZIP, this step is required.** A ZIP file forgets which
files are allowed to run, so the launcher arrives inert and double-clicking
it opens it in a text editor instead of starting anything.

Type `chmod +x ` — **including the space at the end** — then drag
`launch_yumusic.command` from the Finder window into the Terminal window.
macOS fills in its location for you. Then press Return.

The whole line ends up looking something like:

```
chmod +x /Users/yourname/Documents/yumusic-mac-mlx/launch_yumusic.command
```

Nothing is printed. That means it worked.

---

## 6️⃣ The first launch, and Apple's warning

Double-click **launch_yumusic.command**.

**The first time, macOS will refuse**, with a message about an unidentified
developer. This is expected and it happens to every downloaded script that is
not signed with a paid Apple developer certificate. To get past it:

**Right-click** (or Control-click) **launch_yumusic.command → Open**, then
**Open** again in the dialog that appears. You only ever do this once.

If the right-click route does not offer *Open*: go to **System Settings →
Privacy & Security**, scroll down, and there will be a line about
`launch_yumusic.command` being blocked, with an **Open Anyway** button.

**What you should then see:** a Terminal window that fills with start-up
messages, and after a few seconds your browser opens at
`http://127.0.0.1:7870`.

That address is your own Mac talking to itself. Nothing is being sent
anywhere except the one download in step 3.

---

## 7️⃣ The first render

Type a style prompt — `English, indie pop, bright acoustic guitar, warm lead
vocal` will do — write a few lines of lyrics or tick **Instrumental**, and
click **Generate**.

A short track takes a couple of minutes on an Apple Silicon Mac. The Log at
the bottom of the page shows what is happening while you wait.

---

## ➕ Optional extras

None of these are required — the app renders WAV files and works fully
without any of them. Add whichever ones you actually want.

### Drafting a style prompt from a rough idea

One panel on the page can turn a rough idea (*"a sad the cure track, slow,
with choir at the end"*) into a proper style prompt, using a small language
model running locally on your Mac through [Ollama](https://ollama.com).

```
brew install ollama
ollama pull gemma2:9b
```

If you do not have Homebrew yet:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

That is about 5.5 GB and a few minutes. The panel finds Ollama automatically
and the drafting field stops being greyed out.

### MP3/FLAC export, MIDI export, and the sheet-music preview

**These are three separate tools, each optional on its own.** Skip any of
them and the matching checkbox or panel still appears — it just writes one
line to the Log telling you what's missing and what to run, instead of
failing quietly. All three come from Homebrew:

```
brew install ffmpeg    # for the "Also save MP3" / "Also save FLAC" boxes
brew install abcmidi   # for the "Also save MIDI" box (needs the abc2midi tool it provides)
brew install abcm2ps   # for the sheet-music preview under "Results"
```

If you do not have Homebrew yet:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install any subset of the three — the app checks for each independently at
the moment it needs it, not all at once at startup.

---

## 🆘 If something goes wrong

| What you see | What it means |
|---|---|
| `command not found: hf` | Step 2️⃣ did not finish, or this Terminal window was not the one where you ran `source ~/YuE/venv/bin/activate`. |
| Launcher says it can't find the model repo | Step 3️⃣ did not finish, or the model lives somewhere other than `~/YuE/YuE2-3B-MLX` — set `YUE_REPO` to tell it where. |
| Double-clicking the launcher opens a text editor | Step 5️⃣. The executable bit was lost in the ZIP. |
| `unidentified developer` | Step 6️⃣. Right-click → Open. |
| Model variant menu is empty | No variant folder was found under the model repo. Re-run step 3️⃣. |
| An out-of-memory error during a render | Try the 4-bit variant, and a shorter duration. |
| Ticked "Also save MP3"/FLAC/MIDI but the file isn't there | Check the **Log** box on the page — it names the missing tool and the `brew install` command for it. See *Optional extras* above. |
| Anything else | Open an issue on GitHub with what the Terminal window printed — that is usually enough to answer you. |

---

## 💾 Installing somewhere other than the internal disk

Set an environment variable before launching:

```
export YUE_REPO="/Volumes/YourDrive/YuE/YuE2-3B-MLX"
```

**One warning from experience: the drive must be formatted APFS or Mac OS
Extended (HFS+).** A Python environment does not work on exFAT, which is what
most drives are formatted as when you buy them.

---

## 🗑️ Removing all of this

Delete the folder `~/YuE` and the folder you unzipped this into. That is
everything: no system files are touched, nothing is installed globally.

---

## ⚡ The short version

For people who already have `git`, Python and a Terminal habit:

```
mkdir -p ~/YuE && python3 -m venv ~/YuE/venv
source ~/YuE/venv/bin/activate
pip install mlx tiktoken numpy huggingface_hub gradio
cd ~/YuE && hf download ahmadw/YuE2-3B-MLX --include "*.py" --include "8bit/*" --local-dir YuE2-3B-MLX
git clone https://github.com/Quick-Eyed-Sky/yumusic-mac-mlx.git
chmod +x yumusic-mac-mlx/launch_yumusic.command
open yumusic-mac-mlx/launch_yumusic.command
```

The launcher honours `YUE_REPO` and `YUMUSIC2_PORT`, so nothing has to live
where this guide puts it.
