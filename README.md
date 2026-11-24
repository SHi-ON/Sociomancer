# Sociomancer
Social Media Kit

## OpenAI

`openai/delete_library_files.py` lists and deletes files stored in your
OpenAI account through the official Files API (API, Codex, and Assistants
uploads). It uses only the standard library.

```sh
export OPENAI_API_KEY="sk-..."
python openai/delete_library_files.py --list
python openai/delete_library_files.py --delete file-abc123 --yes
python openai/delete_library_files.py --all --dry-run
```

The ChatGPT consumer Library (chatgpt.com sidebar, Android app) has no
public API. Deleting a chat does not delete its Library files.

`openai/delete_chatgpt_library_files.py` automates the same Library
deletion through the backend endpoints used by the
[ChatGPT-bulk-delete-chats](https://github.com/johnvouros/ChatGPT-bulk-delete-chats)
extension, with the same login-session credentials. Export your
chatgpt.com cookies, then run it from your own machine and network
(ChatGPT bot protection blocks most server IPs):

```sh
export CHATGPT_COOKIES='<paste Cookie header from devtools>'
python openai/delete_chatgpt_library_files.py --list
python openai/delete_chatgpt_library_files.py --all --dry-run
python openai/delete_chatgpt_library_files.py --all --yes
```

On a machine where Codex CLI is logged in, `--codex-auth` reuses that
session instead of cookies:

```sh
python openai/delete_chatgpt_library_files.py --list --codex-auth
```

`openai/generate_image.py` generates an image through the official Images API
(`POST /v1/images/generations` with a model and prompt). GPT image models
return base64 image bytes, which the script decodes and writes to a file.
It uses only the standard library.

```sh
export OPENAI_API_KEY="sk-..."
python openai/generate_image.py "a red lighthouse at dusk" --out lighthouse.png
```

`openai/transcribe_chatgpt_audio.py` transcribes an audio file through the
same batch endpoint the ChatGPT web client uses
(`POST /backend-api/transcribe`, multipart `file` + `language` fields,
`{"text": ...}` back). It uses only the standard library.

```sh
python openai/transcribe_chatgpt_audio.py voice-memo.m4a --codex-auth
python openai/transcribe_chatgpt_audio.py interview.mp3 --language en --json
```

The endpoint takes only `file` + `language`: any extra multipart field
(such as a guiding `prompt`) makes the server return 500, and there is no
speaker-label output, so diarization-style labels cannot be requested from
it. Very long files are also rejected (a 31 min / 31 MB file fails fast);
split those into ~5 minute chunks first and transcribe each chunk.

The web client's live mic dictation instead streams PCM16 frames over
`wss://chatgpt.com/backend-api/dictation/stream`
(`session.start` / `audio.append` messages, `chatgpt-dictation` protocol);
one-shot file transcription does not need it.

After a successful deletion batch, the script waits briefly and empties the
entire file trash. This permanently deletes all files already in trash as well
as the files from the batch. It does not empty trash for listing, dry runs,
empty selections, or failed deletion batches. `--yes` confirms both deletion
and permanent trash removal. If the trash request times out, check the trash
in ChatGPT before trying again because the request may still have succeeded.
