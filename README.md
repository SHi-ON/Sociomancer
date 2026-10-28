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
