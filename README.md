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
public API. Delete those files by hand: open Library, select the file,
then Delete. Deleting a chat does not delete its Library files.
